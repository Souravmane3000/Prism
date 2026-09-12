"""Local-development Test Runner contract — skipped is never PASS."""

from unittest.mock import AsyncMock, patch

import pytest


def _make_config(token: str = "ghp_faketoken1234567890") -> dict:
    return {"configurable": {"thread_id": "run-001", "github_token": token}}


@pytest.fixture()
def mock_runner_supabase():
    with (
        patch("backend.agents.test_runner.update_run_status", new=AsyncMock()),
        patch("backend.agents.test_runner.emit_event", new=AsyncMock()),
        patch("backend.agents.test_runner.emit_error_event", new=AsyncMock()),
    ):
        yield


class TestLocalDevelopmentDoesNotSkipAsPass:
    @pytest.mark.asyncio
    async def test_missing_patch_is_not_pass(self, mock_runner_supabase):
        from backend.agents.test_runner import test_runner_node
        from backend.test_outcome import classify_test_results

        with patch("backend.agents.test_runner._use_modal_sandbox", return_value=False):
            result = await test_runner_node(
                {"run_id": "run-001", "repo_url": "https://github.com/owner/repo", "verification_attempts": 0},
                _make_config(),
            )

        assert result["all_tests_passed"] is False
        assert result["verification_attempts"] == 1
        assert classify_test_results(result["test_results"]) in {"did_not_run", "failed"}
