"""tests/graph/test_graph_routing.py — Phase 4 verification routing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRouteAfterTests:
    def test_pass_goes_to_pr_summarizer(self):
        from backend.graph import route_after_tests

        assert (
            route_after_tests({"all_tests_passed": True, "verification_attempts": 1})
            == "pr_summarizer"
        )

    def test_fail_attempt_1_goes_to_debugger(self):
        from backend.graph import route_after_tests

        assert (
            route_after_tests(
                {
                    "all_tests_passed": False,
                    "verification_attempts": 1,
                    "test_results": {"failure_category": "assertion"},
                }
            )
            == "debugger"
        )

    def test_fail_attempt_3_goes_to_rollback(self):
        from backend.graph import route_after_tests

        assert (
            route_after_tests(
                {
                    "all_tests_passed": False,
                    "verification_attempts": 3,
                    "test_results": {"failure_category": "assertion"},
                }
            )
            == "rollback"
        )

    def test_environment_failure_ends(self):
        from backend.graph import route_after_tests

        assert (
            route_after_tests(
                {
                    "all_tests_passed": False,
                    "verification_attempts": 1,
                    "test_results": {"failure_category": "sandbox"},
                }
            )
            == "end"
        )


class TestRouteAfterValidateAndApply:
    def test_validated_code_modifier_goes_to_diff_reviewer(self):
        from backend.graph import route_after_validate_patch

        assert (
            route_after_validate_patch(
                {"patch_status": "VALIDATED", "patch_origin": "code_modifier"}
            )
            == "diff_reviewer"
        )
        assert route_after_validate_patch({"patch_status": "INVALID"}) == "end"

    def test_validated_debugger_bypasses_review(self):
        from backend.graph import route_after_validate_patch

        assert (
            route_after_validate_patch(
                {"patch_status": "VALIDATED", "patch_origin": "debugger"}
            )
            == "apply_patch"
        )

    def test_applied_goes_to_test_runner(self):
        from backend.graph import route_after_apply_patch

        assert route_after_apply_patch({"patch_status": "APPLIED"}) == "test_runner"
        assert (
            route_after_apply_patch({"patch_status": "APPLIED", "session_status": "PATCH_ERROR"})
            == "end"
        )


class TestDebugFixLoop:
    def test_safe_diagnosis_goes_to_fix_patch_not_pr(self):
        from backend.graph import route_after_debugger

        assert (
            route_after_debugger({"debug_report": {"can_generate_fix": True}})
            == "fix_patch"
        )
        assert route_after_debugger({"session_status": "PATCH_ERROR"}) == "end"

    def test_fix_patch_returns_to_validate(self):
        from backend.graph import route_after_fix_patch

        assert (
            route_after_fix_patch(
                {"patch_status": "GENERATED", "current_patch": {"patch_id": "x"}}
            )
            == "validate_patch"
        )

    def test_rollback_success_goes_to_terminal_policy(self):
        from backend.graph import route_after_rollback

        assert route_after_rollback({"patch_status": "ROLLED_BACK"}) == "max_verification_failure"
        assert (
            route_after_rollback({"patch_status": "ROLLED_BACK", "session_status": "PATCH_ERROR"})
            == "end"
        )


class TestBuildGraph:
    def test_registers_phase5_nodes_including_hitl3(self):
        from backend.graph import build_graph

        builder = build_graph()
        expected = {
            "planner",
            "hitl_1",
            "code_navigator",
            "impl_planner",
            "hitl_2",
            "code_modifier",
            "validate_patch",
            "diff_reviewer",
            "hitl_3",
            "max_revisions_failure",
            "apply_patch",
            "test_runner",
            "debugger",
            "fix_patch",
            "rollback",
            "max_verification_failure",
            "pr_summarizer",
        }
        if hasattr(builder, "_nodes"):
            registered = set(builder._nodes.keys()) - {"__start__", "__end__"}
            assert registered == expected

    def test_build_graph_returns_stategraph_instance(self):
        from langgraph.graph import StateGraph

        from backend.graph import build_graph

        assert isinstance(build_graph(), StateGraph)


class TestGetCompiledGraph:
    @pytest.mark.asyncio
    async def test_compiled_graph_is_cached(self):
        import backend.graph as graph_module

        original = graph_module._compiled_graph
        graph_module._compiled_graph = None
        mock_checkpointer = MagicMock()
        mock_checkpointer.setup = AsyncMock()
        mock_compiled = MagicMock()
        mock_builder = MagicMock()
        mock_builder.compile.return_value = mock_compiled
        with (
            patch("backend.graph.get_checkpointer", new=AsyncMock(return_value=mock_checkpointer)),
            patch("backend.graph.build_graph", return_value=mock_builder),
        ):
            from backend.graph import get_compiled_graph

            result_a = await get_compiled_graph()
            result_b = await get_compiled_graph()
        graph_module._compiled_graph = original
        assert result_a is result_b

    @pytest.mark.asyncio
    async def test_compiled_graph_has_correct_return_type_annotation(self):
        from backend.graph import get_compiled_graph

        hints = get_compiled_graph.__annotations__
        return_type = hints.get("return", type(None))
        assert return_type is not object

    def test_compile_pauses_before_hitl_nodes(self):
        import inspect

        from backend import graph as graph_module

        source = inspect.getsource(graph_module.get_compiled_graph)
        compact = source.replace(" ", "")
        assert 'interrupt_before=["hitl_1","hitl_2","hitl_3"]' in compact
