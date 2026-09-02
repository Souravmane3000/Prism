"""tests/agents/test_hitl.py — HITL nodes are passthrough; pause happens via interrupt_before."""

import pytest


class TestHitl1Node:
    @pytest.mark.asyncio
    async def test_passthrough_marks_planner_approved(self):
        from backend.agents.hitl import hitl_1_node

        result = await hitl_1_node(
            {
                "run_id": "run-001",
                "subtasks": [
                    {
                        "id": "st-1",
                        "title": "T",
                        "description": "D",
                        "dependencies": [],
                        "likely_files": [],
                        "complexity": "low",
                    }
                ],
            }
        )
        assert result["planner_approved"] is True
        assert result["current_agent"] == "hitl_1"
        assert "github_token" not in result

    @pytest.mark.asyncio
    async def test_passthrough_does_not_echo_token_from_state(self):
        from backend.agents.hitl import hitl_1_node

        result = await hitl_1_node({"run_id": "run-001", "subtasks": []})
        assert "github_token" not in result
        assert "github_token" not in str(result)


class TestHitl2Node:
    @pytest.mark.asyncio
    async def test_passthrough_marks_impl_approved(self):
        from backend.agents.hitl import hitl_2_node

        plan = [{"subtask_id": "st-1", "steps": []}]
        result = await hitl_2_node(
            {"run_id": "run-001", "implementation_plan": plan}
        )
        assert result["impl_approved"] is True
        assert result["current_agent"] == "hitl_2"
        assert "github_token" not in result
