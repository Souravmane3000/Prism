"""tests/agents/test_test_runner.py — Tests for backend/agents/test_runner.py"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.fixture()
def force_modal_sandbox():
    """Sandbox-path tests assume production Modal execution."""
    with patch("backend.agents.test_runner._use_modal_sandbox", return_value=True):
        yield


class TestBuildCloneUrl:
    def test_injects_token_into_url(self):
        """_build_clone_url injects the PAT into the HTTPS clone URL."""
        from backend.agents.test_runner import _build_clone_url

        url = _build_clone_url("https://github.com/owner/repo", "ghp_mypat123")
        assert "ghp_mypat123" in url
        assert "x-token:" in url
        assert "@github.com" in url

    def test_appends_git_suffix(self):
        """_build_clone_url ensures the URL ends with .git."""
        from backend.agents.test_runner import _build_clone_url

        url = _build_clone_url("https://github.com/owner/repo", "tok")
        assert url.endswith(".git")

    def test_token_not_in_safe_url_log(self):
        """After redaction, token does not appear in safe_url."""
        import re
        from backend.agents.test_runner import _build_clone_url

        token = "ghp_supersecret123456"
        clone_url = _build_clone_url("https://github.com/owner/repo", token)
        safe_url = re.sub(r"x-token:[^@]+@", "x-token:[REDACTED]@", clone_url)
        assert token not in safe_url


class TestDetectFramework:
    def test_returns_pytest_for_pytest_ini(self):
        from backend.agents.test_runner import _detect_framework
        assert _detect_framework("pytest.ini\nsetup.py") == "pytest"

    def test_returns_pytest_for_conftest(self):
        from backend.agents.test_runner import _detect_framework
        assert _detect_framework("conftest.py\nrequirements.txt") == "pytest"

    def test_returns_pytest_for_pyproject_toml(self):
        from backend.agents.test_runner import _detect_framework
        assert _detect_framework("pyproject.toml\nREADME.md") == "pytest"

    def test_returns_jest_for_package_json(self):
        from backend.agents.test_runner import _detect_framework
        assert _detect_framework("package.json\nindex.js") == "jest"

    def test_returns_unknown_for_empty_input(self):
        from backend.agents.test_runner import _detect_framework
        assert _detect_framework("") == "unknown"


class TestParsePytestJson:
    def test_extracts_passed_and_failed_from_valid_json(self):
        """_parse_pytest_json correctly parses valid pytest-json-report output."""
        from backend.agents.test_runner import _parse_pytest_json

        report = {
            "tests": [
                {"nodeid": "test_foo::test_ok", "outcome": "passed"},
                {"nodeid": "test_bar::test_fail", "outcome": "failed",
                 "call": {"longrepr": "AssertionError: expected True"}},
            ]
        }
        passed, failed = _parse_pytest_json(json.dumps(report), "")
        assert "test_foo::test_ok" in passed
        assert len(failed) == 1
        assert failed[0]["name"] == "test_bar::test_fail"

    def test_uses_summary_when_tests_array_empty(self):
        """json-report summary counts are used when tests[] is missing."""
        from backend.agents.test_runner import _parse_pytest_json

        report = {"summary": {"passed": 2, "failed": 1, "total": 3}, "tests": []}
        passed, failed = _parse_pytest_json(json.dumps(report), "")
        assert len(passed) == 2
        assert len(failed) == 1

    def test_falls_back_to_stdout_parsing_when_json_empty(self):
        """Falls back to stdout line-parsing when JSON report is empty."""
        from backend.agents.test_runner import _parse_pytest_json

        stdout = "test_foo::test_ok PASSED\ntest_bar::test_fail FAILED\n"
        passed, failed = _parse_pytest_json("{}", stdout)
        assert any("PASSED" in p for p in passed)
        assert len(failed) >= 1


class TestParseJestJson:
    def test_extracts_passed_and_failed(self):
        """_parse_jest_json extracts passed and failed from valid Jest JSON."""
        from backend.agents.test_runner import _parse_jest_json

        report = {
            "testResults": [
                {
                    "testResults": [
                        {"fullName": "test A", "status": "passed"},
                        {"fullName": "test B", "status": "failed",
                         "failureMessages": ["Expected true, got false"]},
                    ]
                }
            ]
        }
        passed, failed = _parse_jest_json(json.dumps(report))
        assert "test A" in passed
        assert len(failed) == 1
        assert failed[0]["name"] == "test B"


class TestTestRunnerNode:
    @pytest.mark.asyncio
    async def test_empty_collection_is_not_all_tests_passed(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        """0 collected tests with exit 0 is not a green suite — Debugger must run."""
        stdout = "PRISM_FRAMEWORK=pytest\nPRISM_PYTEST_EXIT=5\nPRISM_REPORT_START\n{}\nPRISM_REPORT_END\n"

        with patch(
            "asyncio.to_thread",
            new=AsyncMock(return_value=(stdout, "", 0)),
        ):
            from backend.agents.test_runner import test_runner_node

            result = await test_runner_node(
                {
                    "run_id": "run-001",
                    "repo_url": "https://github.com/owner/repo",
                },
                _make_config(),
            )

        assert result["all_tests_passed"] is False
        assert result["test_results"]["failed_count"] >= 1
        assert result["test_results"]["exit_code"] == 5

    @pytest.mark.asyncio
    async def test_returns_all_tests_passed_true_when_tests_pass(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        """When tests actually pass, all_tests_passed=True."""
        stdout = (
            "PRISM_FRAMEWORK=pytest\n"
            "PRISM_PYTEST_EXIT=0\n"
            "PRISM_REPORT_START\n"
            '{"tests":[{"nodeid":"test_ok","outcome":"passed"}]}\n'
            "PRISM_REPORT_END\n"
        )

        with patch(
            "asyncio.to_thread",
            new=AsyncMock(return_value=(stdout, "", 0)),
        ):
            from backend.agents.test_runner import test_runner_node

            result = await test_runner_node(
                {
                    "run_id": "run-001",
                    "repo_url": "https://github.com/owner/repo",
                },
                _make_config(),
            )

        assert result["all_tests_passed"] is True
        assert result["current_agent"] == "test_runner"

    @pytest.mark.asyncio
    async def test_persists_full_test_results_in_agent_output(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        """Complete-phase payload must include failed/passed lists for the Debug tab."""
        saved: list[tuple[str, dict]] = []

        async def capture_save(run_id, agent, payload, phase):
            saved.append((phase, payload))

        stdout = (
            "PRISM_FRAMEWORK=pytest\n"
            "PRISM_REPORT_START\n"
            '{"tests":[{"nodeid":"test_ok","outcome":"passed"}]}\n'
            "PRISM_REPORT_END\n"
        )

        with (
            patch("asyncio.to_thread", new=AsyncMock(return_value=(stdout, "", 0))),
            patch("backend.agents.test_runner.save_agent_output", side_effect=capture_save),
            patch("backend.agents.test_runner.update_run_status", new=AsyncMock()),
        ):
            from backend.agents.test_runner import test_runner_node

            await test_runner_node(
                {"run_id": "run-001", "repo_url": "https://github.com/owner/repo"},
                _make_config(),
            )

        complete_payloads = [p for phase, p in saved if phase == "complete"]
        assert complete_payloads
        results = complete_payloads[-1]["test_results"]
        assert "failed" in results
        assert "passed" in results
        assert isinstance(results["failed"], list)
        assert "all_tests_passed" in complete_payloads[-1]

    @pytest.mark.asyncio
    async def test_returns_error_and_false_on_sandbox_failure(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        """When sandbox raises, returns all_tests_passed=False and error is set."""
        with patch(
            "asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("Sandbox failed to start")),
        ):
            from backend.agents.test_runner import test_runner_node

            state = {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
            }

            result = await test_runner_node(state, _make_config())

        assert result["all_tests_passed"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sandbox_always_runs_in_thread(
        self, mock_runner_supabase, force_modal_sandbox
    ):
        """Modal Sandbox operations run in asyncio.to_thread (non-blocking)."""
        to_thread_calls = []
        stdout = "PRISM_FRAMEWORK=unknown\nPRISM_REPORT_START\n{}\nPRISM_REPORT_END\n"

        original_to_thread = AsyncMock(return_value=(stdout, "", 0))

        async def capture_to_thread(fn, *args, **kwargs):
            to_thread_calls.append(fn)
            return await original_to_thread(fn, *args, **kwargs)

        with patch("asyncio.to_thread", side_effect=capture_to_thread):
            from backend.agents.test_runner import test_runner_node

            state = {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
            }

            await test_runner_node(state, _make_config())

        assert len(to_thread_calls) >= 1, "asyncio.to_thread must be called for sandbox execution"


class TestSuiteAllPassed:
    def test_empty_collection_is_false(self):
        from backend.agents.test_runner import _suite_all_passed

        assert _suite_all_passed([], [], 0) is False

    def test_one_passed_test_is_true(self):
        from backend.agents.test_runner import _suite_all_passed

        assert _suite_all_passed(["test_ok"], [], 0) is True

    def test_nonzero_exit_is_false(self):
        from backend.agents.test_runner import _suite_all_passed

        assert _suite_all_passed(["test_ok"], [], 1) is False


class TestPytestExitFromStdout:
    def test_prefers_marker_over_sandbox_exit(self):
        from backend.agents.test_runner import _pytest_exit_from_stdout

        stdout = "PRISM_FRAMEWORK=pytest\nPRISM_PYTEST_EXIT=5\n{}\n"
        assert _pytest_exit_from_stdout(stdout, 0) == 5

    def test_falls_back_to_sandbox_exit(self):
        from backend.agents.test_runner import _pytest_exit_from_stdout

        assert _pytest_exit_from_stdout("no marker\n", 0) == 0

