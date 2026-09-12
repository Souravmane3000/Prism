"""tests/agents/test_test_runner.py — Tests for backend/agents/test_runner.py"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.patch.structured import build_structured_patch


def _make_config(token: str = "ghp_faketoken1234567890") -> dict:
    return {"configurable": {"thread_id": "run-001", "github_token": token}}


def _sample_patch() -> dict:
    return build_structured_patch(
        baseline_sha="abc123",
        unified_diff="diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-old\n+new\n",
        modified_files=[{"path": "mod.py", "operation": "modify", "old_path": None}],
    )


def _state(**extra):
    body = {
        "run_id": "run-001",
        "repo_url": "https://github.com/owner/repo",
        "baseline_sha": "abc123",
        "current_patch": _sample_patch(),
        "verification_attempts": 0,
        "patch_status": "APPLIED",
    }
    body.update(extra)
    return body


@pytest.fixture()
def mock_runner_supabase():
    with (
        patch("backend.agents.test_runner.update_run_status", new=AsyncMock()),
        patch("backend.agents.test_runner.emit_event", new=AsyncMock()),
        patch("backend.agents.test_runner.emit_error_event", new=AsyncMock()),
    ):
        yield


@pytest.fixture()
def force_modal_sandbox():
    with patch("backend.agents.test_runner._use_modal_sandbox", return_value=True):
        yield


class TestBuildCloneUrl:
    def test_injects_token_into_url(self):
        from backend.agents.test_runner import _build_clone_url

        url = _build_clone_url("https://github.com/owner/repo", "ghp_mypat123")
        assert "ghp_mypat123" in url
        assert "x-token:" in url


class TestDetectFramework:
    def test_returns_pytest_for_pytest_ini(self):
        from backend.agents.test_runner import _detect_framework

        assert _detect_framework("pytest.ini\nsetup.py") == "pytest"


class TestParsePytestJson:
    def test_extracts_passed_and_failed_from_valid_json(self):
        from backend.agents.test_runner import _parse_pytest_json

        report = {
            "tests": [
                {"nodeid": "test_foo::test_ok", "outcome": "passed"},
                {
                    "nodeid": "test_bar::test_fail",
                    "outcome": "failed",
                    "call": {"longrepr": "AssertionError: expected True"},
                },
            ]
        }
        passed, failed = _parse_pytest_json(json.dumps(report), "")
        assert "test_foo::test_ok" in passed
        assert failed[0]["name"] == "test_bar::test_fail"


class TestTestRunnerNode:
    @pytest.mark.asyncio
    async def test_empty_collection_is_not_all_tests_passed(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        stdout = "PRISM_FRAMEWORK=pytest\nPRISM_PYTEST_EXIT=5\nPRISM_REPORT_START\n{}\nPRISM_REPORT_END\n"
        with patch("asyncio.to_thread", new=AsyncMock(return_value=(stdout, "", 0))):
            from backend.agents.test_runner import test_runner_node

            result = await test_runner_node(_state(), _make_config())
        assert result["all_tests_passed"] is False
        assert result["verification_attempts"] == 1
        assert result["test_results"]["failed_count"] >= 1

    @pytest.mark.asyncio
    async def test_increments_attempt_and_marks_verified_on_pass(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        stdout = (
            "PRISM_FRAMEWORK=pytest\nPRISM_PYTEST_EXIT=0\nPRISM_REPORT_START\n"
            '{"tests":[{"nodeid":"test_ok","outcome":"passed"}]}\nPRISM_REPORT_END\n'
        )
        with patch("asyncio.to_thread", new=AsyncMock(return_value=(stdout, "", 0))):
            from backend.agents.test_runner import test_runner_node

            result = await test_runner_node(_state(), _make_config())
        assert result["all_tests_passed"] is True
        assert result["verification_attempts"] == 1
        assert result["patch_status"] == "VERIFIED"

    @pytest.mark.asyncio
    async def test_missing_patch_is_environment_failure(self, mock_runner_supabase):
        from backend.agents.test_runner import test_runner_node

        result = await test_runner_node(
            {"run_id": "run-001", "repo_url": "https://github.com/owner/repo", "verification_attempts": 0},
            _make_config(),
        )
        assert result["all_tests_passed"] is False
        assert result["test_results"]["failure_category"] == "sandbox"
        assert result["verification_attempts"] == 1

    @pytest.mark.asyncio
    async def test_refuses_fourth_attempt(self, mock_runner_supabase):
        from backend.agents.test_runner import test_runner_node

        result = await test_runner_node(_state(verification_attempts=3), _make_config())
        assert result["all_tests_passed"] is False
        assert "cannot exceed" in (result.get("error") or "")
        assert result.get("session_status") != "FAILED_MAX_VERIFICATION"

    @pytest.mark.asyncio
    async def test_truncates_and_sanitizes_output(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        huge = "ok ghp_abcdefghijklmnopqrstuvwxyz " + ("x" * 60_000)
        stdout = (
            "PRISM_FRAMEWORK=pytest\nPRISM_PYTEST_EXIT=0\nPRISM_REPORT_START\n"
            '{"tests":[{"nodeid":"test_ok","outcome":"passed"}]}\nPRISM_REPORT_END\n'
            + huge
        )
        with patch("asyncio.to_thread", new=AsyncMock(return_value=(stdout, "", 0))):
            from backend.agents.test_runner import test_runner_node

            result = await test_runner_node(_state(), _make_config())
        stored = result["test_results"]["stdout"]
        assert result["test_results"]["truncated"] is True
        assert len(stored) <= 50_200
        assert "ghp_" not in stored

    @pytest.mark.asyncio
    async def test_timeout_sets_category(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        with patch(
            "backend.agents.test_runner._run_modal",
            new=AsyncMock(return_value=("", "pytest timed out", 124, True)),
        ):
            from backend.agents.test_runner import test_runner_node

            result = await test_runner_node(_state(), _make_config())
        assert result["all_tests_passed"] is False
        assert result["test_results"]["failure_category"] == "timeout"

    @pytest.mark.asyncio
    async def test_returns_error_and_false_on_sandbox_failure(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        with patch(
            "asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("Sandbox failed to start")),
        ):
            from backend.agents.test_runner import test_runner_node

            result = await test_runner_node(_state(), _make_config())
        assert result["all_tests_passed"] is False
        assert result["test_results"]["failure_category"] in {"sandbox", "timeout"}
        assert result["verification_attempts"] == 1


class TestSuiteAllPassed:
    def test_empty_collection_is_false(self):
        from backend.agents.test_runner import _suite_all_passed

        assert _suite_all_passed([], [], 0) is False

    def test_one_passed_test_is_true(self):
        from backend.agents.test_runner import _suite_all_passed

        assert _suite_all_passed(["test_ok"], [], 0) is True
