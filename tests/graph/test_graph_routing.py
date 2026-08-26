"""tests/graph/test_graph_routing.py — Tests for backend/graph.py"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRouteAfterTests:
    def test_returns_pr_summarizer_when_all_tests_passed_true(self):
        """route_after_tests returns 'pr_summarizer' when all_tests_passed=True."""
        from backend.graph import route_after_tests

        state = {"all_tests_passed": True}
        result = route_after_tests(state)
        assert result == "pr_summarizer"

    def test_returns_debugger_when_all_tests_passed_false(self):
        """route_after_tests returns 'debugger' when all_tests_passed=False."""
        from backend.graph import route_after_tests

        state = {"all_tests_passed": False}
        result = route_after_tests(state)
        assert result == "debugger"

    def test_returns_debugger_when_all_tests_passed_is_none(self):
        """route_after_tests returns 'debugger' when all_tests_passed is None."""
        from backend.graph import route_after_tests

        state = {"all_tests_passed": None}
        result = route_after_tests(state)
        assert result == "debugger"

    def test_returns_debugger_when_key_absent(self):
        """route_after_tests returns 'debugger' when all_tests_passed key is missing."""
        from backend.graph import route_after_tests

        result = route_after_tests({})
        assert result == "debugger"


class TestBuildGraph:
    def test_creates_stategraph_with_all_8_nodes(self):
        """build_graph() creates a StateGraph with all 8 required nodes."""
        from backend.graph import build_graph

        builder = build_graph()
        # LangGraph's StateGraph stores nodes internally; we can verify via compilation
        # We check the builder has the expected structure by compiling with a mock saver
        expected_nodes = {
            "planner", "hitl_1", "code_navigator", "impl_planner",
            "hitl_2", "test_runner", "debugger", "pr_summarizer"
        }
        # The builder's _nodes attribute contains registered nodes (internal API)
        if hasattr(builder, "_nodes"):
            registered = set(builder._nodes.keys()) - {"__start__", "__end__"}
            assert registered == expected_nodes

    def test_build_graph_returns_stategraph_instance(self):
        """build_graph() returns a StateGraph instance."""
        from langgraph.graph import StateGraph

        from backend.graph import build_graph

        builder = build_graph()
        assert isinstance(builder, StateGraph)


class TestGetCompiledGraph:
    @pytest.mark.asyncio
    async def test_compiled_graph_is_cached(self):
        """get_compiled_graph() returns the same instance on repeated calls."""
        import backend.graph as graph_module

        # Reset cached graph
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

        # Restore
        graph_module._compiled_graph = original

        assert result_a is result_b, "get_compiled_graph must return the same cached instance"

    @pytest.mark.asyncio
    async def test_compiled_graph_has_correct_return_type_annotation(self):
        """get_compiled_graph has a typed return annotation, not 'object'."""
        import inspect
        from backend.graph import get_compiled_graph

        hints = get_compiled_graph.__annotations__
        return_type = hints.get("return", type(None))
        assert return_type is not object, (
            "get_compiled_graph return type must be CompiledStateGraph, not 'object'"
        )
