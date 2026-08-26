"""tests/agents/test_implementation_planner.py — Tests for backend/agents/implementation_planner.py"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


VALID_PLAN_JSON = json.dumps({
    "subtask_id": "st-1",
    "steps": [
        {
            "order": 1,
            "file": "backend/main.py",
            "function_or_symbol": "create_app",
            "change_description": "Add SlowAPI rate limiter",
            "rationale": "Integrates with FastAPI",
            "tradeoffs": ["Adds latency"],
        }
    ],
})


@pytest.fixture()
def mock_impl_supabase():
    with (
        patch("backend.agents.implementation_planner.update_run_status", new=AsyncMock()),
        patch("backend.agents.implementation_planner.save_agent_output", new=AsyncMock()),
    ):
        yield


@pytest.fixture()
def mock_impl_llm():
    llm_mock = MagicMock()
    response_mock = MagicMock()
    response_mock.content = VALID_PLAN_JSON
    llm_mock.ainvoke = AsyncMock(return_value=response_mock)
    with patch("backend.agents.implementation_planner.get_llm", return_value=llm_mock):
        yield llm_mock


class TestImplementationPlannerNode:
    @pytest.mark.asyncio
    async def test_returns_implementation_plan_list(
        self, mock_impl_supabase, mock_impl_llm
    ):
        """implementation_planner_node returns an implementation_plan list."""
        from backend.agents.implementation_planner import implementation_planner_node

        state = {
            "run_id": "run-001",
            "subtasks": [
                {
                    "id": "st-1", "title": "Add rate limiting",
                    "description": "Implement rate limiting",
                    "dependencies": [], "likely_files": ["backend/main.py"], "complexity": "medium"
                }
            ],
            "file_map": {"st-1": [{"path": "backend/main.py", "relevance_score": 0.9, "source": "pgvector"}]},
            "file_contents": {"backend/main.py": "from fastapi import FastAPI"},
        }

        result = await implementation_planner_node(state)
        assert "implementation_plan" in result
        assert result["current_agent"] == "impl_planner"
        plan = result["implementation_plan"]
        assert len(plan) == 1
        assert plan[0]["subtask_id"] == "st-1"

    @pytest.mark.asyncio
    async def test_one_plan_item_per_subtask(
        self, mock_impl_supabase, mock_impl_llm
    ):
        """Node produces one ImplementationPlanItem per subtask."""
        from backend.agents.implementation_planner import implementation_planner_node

        subtasks = [
            {"id": f"st-{i}", "title": f"Task {i}", "description": "desc",
             "dependencies": [], "likely_files": [], "complexity": "low"}
            for i in range(1, 4)
        ]
        state = {
            "run_id": "run-001",
            "subtasks": subtasks,
            "file_map": {},
            "file_contents": {},
        }

        result = await implementation_planner_node(state)
        assert len(result["implementation_plan"]) == 3

    @pytest.mark.asyncio
    async def test_llm_parse_failure_produces_fallback_item(
        self, mock_impl_supabase
    ):
        """A bad LLM JSON response produces a fallback plan item, no crash."""
        bad_llm = MagicMock()
        response_mock = MagicMock()
        response_mock.content = "this is not json at all"
        bad_llm.ainvoke = AsyncMock(return_value=response_mock)

        with patch("backend.agents.implementation_planner.get_llm", return_value=bad_llm):
            from backend.agents.implementation_planner import implementation_planner_node

            state = {
                "run_id": "run-001",
                "subtasks": [{"id": "st-1", "title": "T", "description": "D",
                               "dependencies": [], "likely_files": [], "complexity": "low"}],
                "file_map": {},
                "file_contents": {},
            }

            result = await implementation_planner_node(state)

        # Should still produce a plan item with fallback steps
        assert len(result["implementation_plan"]) == 1
        assert result["implementation_plan"][0]["steps"][0]["file"] == "unknown"

    @pytest.mark.asyncio
    async def test_returns_error_when_no_subtasks(self, mock_impl_supabase):
        """Node returns error when subtasks list is empty."""
        from backend.agents.implementation_planner import implementation_planner_node

        state = {
            "run_id": "run-001",
            "subtasks": [],
            "file_map": {},
            "file_contents": {},
        }

        result = await implementation_planner_node(state)
        assert "error" in result
        assert result["current_agent"] == "impl_planner"

    @pytest.mark.asyncio
    async def test_system_prompt_contains_no_code_rule(self, mock_impl_supabase, mock_impl_llm):
        """The system prompt explicitly forbids writing code."""
        from backend.agents.implementation_planner import _SYSTEM_PROMPT

        assert "Do NOT write any code" in _SYSTEM_PROMPT or "not write any code" in _SYSTEM_PROMPT.lower()

    @pytest.mark.asyncio
    async def test_returns_current_agent_impl_planner(self, mock_impl_supabase, mock_impl_llm):
        """Node returns current_agent='impl_planner'."""
        from backend.agents.implementation_planner import implementation_planner_node

        state = {
            "run_id": "run-001",
            "subtasks": [{"id": "st-1", "title": "T", "description": "D",
                          "dependencies": [], "likely_files": [], "complexity": "low"}],
            "file_map": {},
            "file_contents": {},
        }

        result = await implementation_planner_node(state)
        assert result["current_agent"] == "impl_planner"
