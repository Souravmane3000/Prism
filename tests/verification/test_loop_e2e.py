"""End-to-end verification loop scenarios A–D using an in-memory graph."""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from backend.graph import (
    route_after_apply_patch,
    route_after_debugger,
    route_after_fix_patch,
    route_after_rollback,
    route_after_tests,
    route_after_validate_patch,
)
from backend.verification import increment_verification_attempts


class LoopState(TypedDict, total=False):
    patch_status: str
    patch_origin: str
    verification_attempts: int
    all_tests_passed: bool
    test_results: dict[str, Any]
    session_status: str
    current_agent: str
    current_patch: dict[str, Any]
    debug_report: dict[str, Any]


def _compile_loop(handlers: dict[str, Any]):
    builder = StateGraph(LoopState)

    async def apply_node(state: LoopState) -> dict[str, Any]:
        return handlers["apply"](state)

    async def test_node(state: LoopState) -> dict[str, Any]:
        attempts = increment_verification_attempts(state.get("verification_attempts"))
        update = handlers["test"](state, attempts)
        update["verification_attempts"] = attempts
        return update

    async def debug_node(state: LoopState) -> dict[str, Any]:
        return handlers["debug"](state)

    async def fix_node(state: LoopState) -> dict[str, Any]:
        return handlers["fix"](state)

    async def validate_node(state: LoopState) -> dict[str, Any]:
        # Verification-loop fixtures model debugger-origin recovery (HITL #3 bypass).
        return {
            "patch_status": "VALIDATED",
            "patch_origin": state.get("patch_origin") or "debugger",
        }

    async def rollback_node(state: LoopState) -> dict[str, Any]:
        return handlers["rollback"](state)

    async def max_node(state: LoopState) -> dict[str, Any]:
        return {"session_status": "FAILED_MAX_VERIFICATION"}

    async def pr_node(state: LoopState) -> dict[str, Any]:
        return {"current_agent": "pr_summarizer"}

    builder.add_node("validate_patch", validate_node)
    builder.add_node("apply_patch", apply_node)
    builder.add_node("test_runner", test_node)
    builder.add_node("debugger", debug_node)
    builder.add_node("fix_patch", fix_node)
    builder.add_node("rollback", rollback_node)
    builder.add_node("max_verification_failure", max_node)
    builder.add_node("pr_summarizer", pr_node)

    builder.add_edge(START, "validate_patch")
    builder.add_conditional_edges(
        "validate_patch",
        route_after_validate_patch,
        {"apply_patch": "apply_patch", "diff_reviewer": END, "end": END},
    )
    builder.add_conditional_edges(
        "apply_patch", route_after_apply_patch, {"test_runner": "test_runner", "end": END}
    )
    builder.add_conditional_edges(
        "test_runner",
        route_after_tests,
        {"pr_summarizer": "pr_summarizer", "debugger": "debugger", "rollback": "rollback", "end": END},
    )
    builder.add_conditional_edges(
        "debugger", route_after_debugger, {"fix_patch": "fix_patch", "end": END}
    )
    builder.add_conditional_edges(
        "fix_patch", route_after_fix_patch, {"validate_patch": "validate_patch", "end": END}
    )
    builder.add_conditional_edges(
        "rollback",
        route_after_rollback,
        {"max_verification_failure": "max_verification_failure", "end": END},
    )
    builder.add_edge("pr_summarizer", END)
    builder.add_edge("max_verification_failure", END)
    return builder.compile()


def _invoke_config() -> dict[str, Any]:
    return {"recursion_limit": 40}


@pytest.mark.asyncio
async def test_scenario_a_pass_on_attempt_1():
    calls = {"test": 0, "debug": 0, "fix": 0, "rollback": 0}

    def apply(state):
        return {"patch_status": "APPLIED", "session_status": "RUNNING"}

    def test(state, attempts):
        calls["test"] += 1
        return {
            "all_tests_passed": True,
            "test_results": {"failure_category": None},
            "patch_status": "VERIFIED",
            "session_status": "RUNNING",
        }

    graph = _compile_loop(
        {
            "apply": apply,
            "test": test,
            "debug": lambda s: (_ for _ in ()).throw(AssertionError("debugger")),
            "fix": lambda s: (_ for _ in ()).throw(AssertionError("fix")),
            "rollback": lambda s: (_ for _ in ()).throw(AssertionError("rollback")),
        }
    )
    result = await graph.ainvoke(
        {"patch_status": "VALIDATED", "verification_attempts": 0, "all_tests_passed": False},
        _invoke_config(),
    )
    assert calls["test"] == 1
    assert result["verification_attempts"] == 1
    assert result["current_agent"] == "pr_summarizer"
    assert result["patch_status"] == "VERIFIED"
    assert result["session_status"] == "RUNNING"


@pytest.mark.asyncio
async def test_scenario_b_fail_fix_pass():
    calls = {"test": 0, "debug": 0, "fix": 0}

    def test(state, attempts):
        calls["test"] += 1
        passed = attempts >= 2
        return {
            "all_tests_passed": passed,
            "test_results": {"failure_category": None if passed else "assertion"},
            "patch_status": "VERIFIED" if passed else "APPLIED",
            "session_status": "RUNNING",
        }

    def debug(state):
        calls["debug"] += 1
        return {"debug_report": {"can_generate_fix": True}, "session_status": "RUNNING"}

    def fix(state):
        calls["fix"] += 1
        return {"patch_status": "GENERATED", "current_patch": {"patch_id": "fix1"}}

    graph = _compile_loop(
        {
            "apply": lambda s: {"patch_status": "APPLIED", "session_status": "RUNNING"},
            "test": test,
            "debug": debug,
            "fix": fix,
            "rollback": lambda s: (_ for _ in ()).throw(AssertionError("rollback")),
        }
    )
    result = await graph.ainvoke(
        {
            "patch_status": "VALIDATED",
            "verification_attempts": 0,
            "current_patch": {"patch_id": "orig"},
        },
        _invoke_config(),
    )
    assert calls == {"test": 2, "debug": 1, "fix": 1}
    assert result["verification_attempts"] == 2
    assert result["current_agent"] == "pr_summarizer"


@pytest.mark.asyncio
async def test_scenario_c_pass_on_attempt_3():
    calls = {"test": 0, "debug": 0, "fix": 0}

    def test(state, attempts):
        calls["test"] += 1
        passed = attempts == 3
        return {
            "all_tests_passed": passed,
            "test_results": {"failure_category": None if passed else "assertion"},
            "patch_status": "VERIFIED" if passed else "APPLIED",
            "session_status": "RUNNING",
        }

    def debug(state):
        calls["debug"] += 1
        return {"debug_report": {"can_generate_fix": True}, "session_status": "RUNNING"}

    def fix(state):
        calls["fix"] += 1
        return {"patch_status": "GENERATED", "current_patch": {"patch_id": f"fix{calls['fix']}"}}

    graph = _compile_loop(
        {
            "apply": lambda s: {"patch_status": "APPLIED", "session_status": "RUNNING"},
            "test": test,
            "debug": debug,
            "fix": fix,
            "rollback": lambda s: (_ for _ in ()).throw(AssertionError("no rollback")),
        }
    )
    result = await graph.ainvoke(
        {"patch_status": "VALIDATED", "verification_attempts": 0},
        _invoke_config(),
    )
    assert calls["test"] == 3
    assert calls["debug"] == 2
    assert calls["fix"] == 2
    assert result["verification_attempts"] == 3
    assert result["current_agent"] == "pr_summarizer"


@pytest.mark.asyncio
async def test_scenario_d_fail_three_then_rollback():
    calls = {"test": 0, "debug": 0, "fix": 0, "rollback": 0}

    def test(state, attempts):
        calls["test"] += 1
        return {
            "all_tests_passed": False,
            "test_results": {"failure_category": "assertion"},
            "patch_status": "APPLIED",
        }

    def debug(state):
        calls["debug"] += 1
        return {"debug_report": {"can_generate_fix": True}, "session_status": "RUNNING"}

    def fix(state):
        calls["fix"] += 1
        return {"patch_status": "GENERATED", "current_patch": {"patch_id": "x"}}

    def rollback(state):
        calls["rollback"] += 1
        return {"patch_status": "ROLLED_BACK", "session_status": "RUNNING"}

    graph = _compile_loop(
        {
            "apply": lambda s: {"patch_status": "APPLIED", "session_status": "RUNNING"},
            "test": test,
            "debug": debug,
            "fix": fix,
            "rollback": rollback,
        }
    )
    result = await graph.ainvoke(
        {"patch_status": "VALIDATED", "verification_attempts": 0},
        _invoke_config(),
    )
    assert calls["test"] == 3
    assert calls["debug"] == 2
    assert calls["fix"] == 2
    assert calls["rollback"] == 1
    assert result["verification_attempts"] == 3
    assert result["session_status"] == "FAILED_MAX_VERIFICATION"
    assert result["patch_status"] == "ROLLED_BACK"


@pytest.mark.asyncio
async def test_debugger_no_safe_fix_does_not_loop():
    calls = {"debug": 0, "test": 0}

    def test(state, attempts):
        calls["test"] += 1
        return {
            "all_tests_passed": False,
            "test_results": {"failure_category": "assertion"},
        }

    def debug(state):
        calls["debug"] += 1
        return {
            "debug_report": {"can_generate_fix": False},
            "session_status": "PATCH_ERROR",
        }

    graph = _compile_loop(
        {
            "apply": lambda s: {"patch_status": "APPLIED", "session_status": "RUNNING"},
            "test": test,
            "debug": debug,
            "fix": lambda s: (_ for _ in ()).throw(AssertionError("no fix")),
            "rollback": lambda s: (_ for _ in ()).throw(AssertionError("no rollback")),
        }
    )
    result = await graph.ainvoke(
        {"patch_status": "VALIDATED", "verification_attempts": 0},
        _invoke_config(),
    )
    assert calls["test"] == 1
    assert calls["debug"] == 1
    assert result["session_status"] == "PATCH_ERROR"
    assert result.get("verification_attempts") == 1


@pytest.mark.asyncio
async def test_apply_failure_skips_test_runner():
    calls = {"test": 0}

    graph = _compile_loop(
        {
            "apply": lambda s: {"session_status": "PATCH_ERROR", "patch_status": "VALIDATED"},
            "test": lambda s, a: calls.__setitem__("test", calls["test"] + 1) or {},
            "debug": lambda s: (_ for _ in ()).throw(AssertionError("debug")),
            "fix": lambda s: (_ for _ in ()).throw(AssertionError("fix")),
            "rollback": lambda s: (_ for _ in ()).throw(AssertionError("rollback")),
        }
    )
    result = await graph.ainvoke(
        {"patch_status": "VALIDATED", "verification_attempts": 0},
        _invoke_config(),
    )
    assert calls["test"] == 0
    assert result["session_status"] == "PATCH_ERROR"
    assert result.get("verification_attempts", 0) == 0


@pytest.mark.asyncio
async def test_rollback_failure_is_not_failed_max_verification():
    def test(state, attempts):
        return {
            "all_tests_passed": False,
            "test_results": {"failure_category": "assertion"},
            "patch_status": "APPLIED",
        }

    graph = _compile_loop(
        {
            "apply": lambda s: {"patch_status": "APPLIED", "session_status": "RUNNING"},
            "test": test,
            "debug": lambda s: {"debug_report": {"can_generate_fix": True}, "session_status": "RUNNING"},
            "fix": lambda s: {"patch_status": "GENERATED", "current_patch": {"patch_id": "x"}},
            "rollback": lambda s: {"session_status": "PATCH_ERROR", "patch_status": "APPLIED"},
        }
    )
    result = await graph.ainvoke(
        {"patch_status": "VALIDATED", "verification_attempts": 0},
        _invoke_config(),
    )
    assert result["session_status"] == "PATCH_ERROR"
    assert result["session_status"] != "FAILED_MAX_VERIFICATION"
    assert result["verification_attempts"] == 3


@pytest.mark.asyncio
async def test_environment_failure_does_not_enter_debugger():
    calls = {"debug": 0, "test": 0}

    def test(state, attempts):
        calls["test"] += 1
        return {
            "all_tests_passed": False,
            "test_results": {"failure_category": "sandbox"},
        }

    graph = _compile_loop(
        {
            "apply": lambda s: {"patch_status": "APPLIED", "session_status": "RUNNING"},
            "test": test,
            "debug": lambda s: calls.__setitem__("debug", calls["debug"] + 1) or {},
            "fix": lambda s: (_ for _ in ()).throw(AssertionError("fix")),
            "rollback": lambda s: (_ for _ in ()).throw(AssertionError("rollback")),
        }
    )
    result = await graph.ainvoke(
        {"patch_status": "VALIDATED", "verification_attempts": 0},
        _invoke_config(),
    )
    assert calls["test"] == 1
    assert calls["debug"] == 0
    assert result["verification_attempts"] == 1
    assert result.get("session_status") != "FAILED_MAX_VERIFICATION"
