"""tests/agents/test_hitl.py — Tests for backend/agents/hitl.py"""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


@pytest.fixture()
def mock_hitl_supabase():
    with (
        patch("backend.agents.hitl.update_run_status", new=AsyncMock()),
        patch("backend.agents.hitl.create_checkpoint", new=AsyncMock(return_value="cp-uuid")),
        patch("backend.agents.hitl.save_agent_output", new=AsyncMock()),
    ):
        yield


@pytest.fixture()
def mock_interrupt():
    """Patch langgraph interrupt to be a no-op (prevents actual graph interruption)."""
    with patch("backend.agents.hitl.interrupt") as mock_int:
        yield mock_int


class TestHitl1Node:
    @pytest.mark.asyncio
    async def test_calls_update_run_status_with_awaiting_approval(
        self, mock_interrupt
    ):
        """hitl_1_node calls update_run_status with 'awaiting_approval'."""
        status_calls = []

        async def capture_status(run_id, status, current_agent, **kwargs):
            status_calls.append(status)

        with (
            patch("backend.agents.hitl.update_run_status", side_effect=capture_status),
            patch("backend.agents.hitl.create_checkpoint", new=AsyncMock(return_value="cp-uuid")),
            patch("backend.agents.hitl.save_agent_output", new=AsyncMock()),
        ):
            from backend.agents.hitl import hitl_1_node

            state = {
                "run_id": "run-001",
                "subtasks": [{"id": "st-1", "title": "T", "description": "D",
                               "dependencies": [], "likely_files": [], "complexity": "low"}],
            }

            await hitl_1_node(state)

        assert "awaiting_approval" in status_calls

    @pytest.mark.asyncio
    async def test_calls_create_checkpoint_with_correct_payload_shape(
        self, mock_interrupt
    ):
        """hitl_1_node calls create_checkpoint with checkpoint='hitl_1'."""
        checkpoint_payloads = []

        async def capture_checkpoint(run_id, name, payload):
            checkpoint_payloads.append((name, payload))
            return "cp-uuid"

        with (
            patch("backend.agents.hitl.update_run_status", new=AsyncMock()),
            patch("backend.agents.hitl.create_checkpoint", side_effect=capture_checkpoint),
            patch("backend.agents.hitl.save_agent_output", new=AsyncMock()),
        ):
            from backend.agents.hitl import hitl_1_node

            state = {
                "run_id": "run-001",
                "subtasks": [{"id": "st-1", "title": "T", "description": "D",
                               "dependencies": [], "likely_files": [], "complexity": "low"}],
            }

            await hitl_1_node(state)

        assert len(checkpoint_payloads) == 1
        name, payload = checkpoint_payloads[0]
        assert name == "hitl_1"
        assert payload["checkpoint"] == "hitl_1"
        assert payload["type"] == "subtask_approval"
        assert "subtasks" in payload

    @pytest.mark.asyncio
    async def test_payload_does_not_contain_github_token(self, mock_interrupt):
        """hitl_1_node checkpoint payload must NOT contain github_token."""
        checkpoint_payloads = []

        async def capture_checkpoint(run_id, name, payload):
            checkpoint_payloads.append(payload)
            return "cp-uuid"

        with (
            patch("backend.agents.hitl.update_run_status", new=AsyncMock()),
            patch("backend.agents.hitl.create_checkpoint", side_effect=capture_checkpoint),
            patch("backend.agents.hitl.save_agent_output", new=AsyncMock()),
        ):
            from backend.agents.hitl import hitl_1_node

            state = {
                "run_id": "run-001",
                "subtasks": [],
            }

            await hitl_1_node(state)

        for payload in checkpoint_payloads:
            assert "github_token" not in payload
            assert "github_token" not in str(payload)

    @pytest.mark.asyncio
    async def test_save_agent_output_called_with_start_phase(
        self, mock_interrupt
    ):
        """hitl_1_node calls save_agent_output with phase='start'."""
        output_calls = []

        async def capture_output(run_id, agent, payload, phase):
            output_calls.append(phase)

        with (
            patch("backend.agents.hitl.update_run_status", new=AsyncMock()),
            patch("backend.agents.hitl.create_checkpoint", new=AsyncMock(return_value="cp-uuid")),
            patch("backend.agents.hitl.save_agent_output", side_effect=capture_output),
        ):
            from backend.agents.hitl import hitl_1_node

            state = {"run_id": "run-001", "subtasks": []}
            await hitl_1_node(state)

        assert "start" in output_calls


class TestHitl2Node:
    @pytest.mark.asyncio
    async def test_calls_update_run_status_with_awaiting_approval(
        self, mock_interrupt
    ):
        """hitl_2_node calls update_run_status with 'awaiting_approval'."""
        status_calls = []

        async def capture_status(run_id, status, current_agent, **kwargs):
            status_calls.append(status)

        with (
            patch("backend.agents.hitl.update_run_status", side_effect=capture_status),
            patch("backend.agents.hitl.create_checkpoint", new=AsyncMock(return_value="cp-uuid")),
            patch("backend.agents.hitl.save_agent_output", new=AsyncMock()),
        ):
            from backend.agents.hitl import hitl_2_node

            state = {
                "run_id": "run-001",
                "implementation_plan": [],
            }

            await hitl_2_node(state)

        assert "awaiting_approval" in status_calls

    @pytest.mark.asyncio
    async def test_checkpoint_payload_contains_implementation_plan(
        self, mock_interrupt
    ):
        """hitl_2_node includes implementation_plan in checkpoint payload."""
        checkpoint_payloads = []

        async def capture_checkpoint(run_id, name, payload):
            checkpoint_payloads.append(payload)
            return "cp-uuid"

        with (
            patch("backend.agents.hitl.update_run_status", new=AsyncMock()),
            patch("backend.agents.hitl.create_checkpoint", side_effect=capture_checkpoint),
            patch("backend.agents.hitl.save_agent_output", new=AsyncMock()),
        ):
            from backend.agents.hitl import hitl_2_node

            plan = [{"subtask_id": "st-1", "steps": []}]
            state = {"run_id": "run-001", "implementation_plan": plan}

            await hitl_2_node(state)

        assert len(checkpoint_payloads) == 1
        assert checkpoint_payloads[0]["checkpoint"] == "hitl_2"
        assert checkpoint_payloads[0]["implementation_plan"] == plan
