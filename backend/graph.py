"""LangGraph StateGraph assembly — Phase 4 verification loop adapter."""

import logging

import backend.config  # noqa: F401 — LangSmith env must exist before LangGraph loads
from backend.config import postgres_connect_kwargs

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from psycopg_pool import AsyncConnectionPool

from backend.agents.apply_patch import apply_patch_node
from backend.agents.code_modifier import code_modifier_node
from backend.agents.code_navigator import code_navigator_node
from backend.agents.debugger import debugger_node
from backend.agents.fix_patch import fix_patch_node
from backend.agents.hitl import hitl_1_node, hitl_2_node
from backend.agents.implementation_planner import implementation_planner_node
from backend.agents.max_verification_failure import max_verification_failure_node
from backend.agents.planner import planner_node
from backend.agents.pr_summarizer import pr_summarizer_node
from backend.agents.rollback import rollback_node
from backend.agents.test_runner import test_runner_node
from backend.agents.validate_patch import validate_patch_node
from backend.state import PrismState
from backend.verification import route_after_verification

logger = logging.getLogger(__name__)


def route_after_code_modifier(state: PrismState) -> str:
    status = state.get("session_status")
    if status in {"CONTEXT_OVERFLOW", "LLM_ERROR", "PATCH_ERROR"}:
        return "end"
    if state.get("patch_status") == "GENERATED" and state.get("current_patch"):
        return "validate_patch"
    return "end"


def route_after_validate_patch(state: PrismState) -> str:
    if state.get("patch_status") == "VALIDATED":
        return "apply_patch"
    return "end"


def route_after_apply_patch(state: PrismState) -> str:
    if state.get("session_status") == "PATCH_ERROR":
        return "end"
    if state.get("patch_status") == "APPLIED":
        return "test_runner"
    return "end"


def route_after_tests(state: PrismState) -> str:
    results = state.get("test_results") or {}
    category = results.get("failure_category") if isinstance(results, dict) else None
    destination = route_after_verification(
        all_tests_passed=state.get("all_tests_passed"),
        verification_attempts=int(state.get("verification_attempts") or 0),
        failure_category=category,
    )
    logger.info("[graph] route_after_tests -> %s", destination)
    return destination


def route_after_debugger(state: PrismState) -> str:
    status = state.get("session_status")
    if status in {"CONTEXT_OVERFLOW", "LLM_ERROR", "PATCH_ERROR"}:
        return "end"
    report = state.get("debug_report") or {}
    if report.get("can_generate_fix"):
        return "fix_patch"
    return "end"


def route_after_fix_patch(state: PrismState) -> str:
    status = state.get("session_status")
    if status in {"CONTEXT_OVERFLOW", "LLM_ERROR", "PATCH_ERROR"}:
        return "end"
    if state.get("patch_status") == "GENERATED" and state.get("current_patch"):
        return "validate_patch"
    return "end"


def route_after_rollback(state: PrismState) -> str:
    if state.get("patch_status") == "ROLLED_BACK" and state.get("session_status") != "PATCH_ERROR":
        return "max_verification_failure"
    return "end"


def build_graph() -> StateGraph:
    builder: StateGraph = StateGraph(PrismState)

    builder.add_node("planner", planner_node)
    builder.add_node("hitl_1", hitl_1_node)
    builder.add_node("code_navigator", code_navigator_node)
    builder.add_node("impl_planner", implementation_planner_node)
    builder.add_node("hitl_2", hitl_2_node)
    builder.add_node("code_modifier", code_modifier_node)
    builder.add_node("validate_patch", validate_patch_node)
    builder.add_node("apply_patch", apply_patch_node)
    builder.add_node("test_runner", test_runner_node)
    builder.add_node("debugger", debugger_node)
    builder.add_node("fix_patch", fix_patch_node)
    builder.add_node("rollback", rollback_node)
    builder.add_node("max_verification_failure", max_verification_failure_node)
    builder.add_node("pr_summarizer", pr_summarizer_node)

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "hitl_1")
    builder.add_edge("hitl_1", "code_navigator")
    builder.add_edge("code_navigator", "impl_planner")
    builder.add_edge("impl_planner", "hitl_2")
    builder.add_edge("hitl_2", "code_modifier")
    builder.add_edge("pr_summarizer", END)
    builder.add_edge("max_verification_failure", END)

    builder.add_conditional_edges(
        "code_modifier",
        route_after_code_modifier,
        {"validate_patch": "validate_patch", "end": END},
    )
    builder.add_conditional_edges(
        "validate_patch",
        route_after_validate_patch,
        {"apply_patch": "apply_patch", "end": END},
    )
    builder.add_conditional_edges(
        "apply_patch",
        route_after_apply_patch,
        {"test_runner": "test_runner", "end": END},
    )
    builder.add_conditional_edges(
        "test_runner",
        route_after_tests,
        {
            "pr_summarizer": "pr_summarizer",
            "debugger": "debugger",
            "rollback": "rollback",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "debugger",
        route_after_debugger,
        {"fix_patch": "fix_patch", "end": END},
    )
    builder.add_conditional_edges(
        "fix_patch",
        route_after_fix_patch,
        {"validate_patch": "validate_patch", "end": END},
    )
    builder.add_conditional_edges(
        "rollback",
        route_after_rollback,
        {"max_verification_failure": "max_verification_failure", "end": END},
    )
    return builder


async def get_checkpointer() -> AsyncPostgresSaver:
    kwargs = postgres_connect_kwargs()
    logger.info(
        "[graph] Opening Postgres pool — host=%s port=%s user=%s db=%s",
        kwargs["host"],
        kwargs["port"],
        kwargs["user"],
        kwargs["dbname"],
    )
    pool = AsyncConnectionPool(
        conninfo="",
        min_size=1,
        max_size=5,
        timeout=10,
        reconnect_timeout=0,
        kwargs=kwargs,
        open=False,
    )
    try:
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        return checkpointer
    except Exception:
        await pool.close()
        raise


_compiled_graph: CompiledStateGraph | None = None


async def get_compiled_graph() -> CompiledStateGraph:
    global _compiled_graph
    if _compiled_graph is None:
        logger.info("[graph] Compiling StateGraph with Supabase checkpointer")
        checkpointer = await get_checkpointer()
        builder = build_graph()
        _compiled_graph = builder.compile(
            checkpointer=checkpointer,
            interrupt_before=["hitl_1", "hitl_2"],
        )
        logger.info("[graph] StateGraph compiled successfully")
    return _compiled_graph
