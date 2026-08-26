"""
Local-development Test Runner contract.

Local uvicorn uses OPENAI_API_KEY / Supabase keys from .env.
Modal is for production backend deploy only — Test Runner must not require
`modal token new` and must not persist Modal auth errors as fake 0/0/exit-1
test results.
"""

from unittest.mock import AsyncMock, patch

import pytest


def _make_config(token: str = "ghp_faketoken1234567890") -> dict:
    return {"configurable": {"thread_id": "run-001", "github_token": token}}


@pytest.fixture()
def mock_runner_supabase():
    with (
        patch("backend.agents.test_runner.update_run_status", new=AsyncMock()),
        patch("backend.agents.test_runner.save_agent_output", new=AsyncMock()),
    ):
        yield


class TestLocalDevelopmentSkipsModal:
    @pytest.mark.asyncio
    async def test_development_does_not_call_modal_or_surface_token_error(
        self, mock_runner_supabase
    ):
        from backend.agents.test_runner import test_runner_node

        with (
            patch("backend.agents.test_runner._use_modal_sandbox", return_value=False),
            patch(
                "backend.agents.test_runner.modal.App.lookup",
                side_effect=AssertionError("Modal must not be called in development"),
            ),
            patch(
                "asyncio.to_thread",
                new=AsyncMock(side_effect=AssertionError("sandbox thread must not run")),
            ),
        ):
            result = await test_runner_node(
                {"run_id": "run-001", "repo_url": "https://github.com/owner/repo"},
                _make_config(),
            )

        tr = result["test_results"]
        stderr = (tr.get("stderr") or "").lower()
        assert tr["framework"] == "skipped"
        assert "token missing" not in stderr
        assert "could not authenticate client" not in stderr
        assert "modal token new" not in stderr
        assert result["all_tests_passed"] is True
        assert result["current_agent"] == "test_runner"

        from backend.test_outcome import classify_test_results

        assert classify_test_results(tr) == "skipped"
