"""
backend/agents/test_runner.py — Test Runner agent node.

Clones the target repository into a Modal.Sandbox, detects the test framework,
runs the full test suite, and returns structured pass/fail results.

SECURITY: User repository code NEVER executes in the main FastAPI process.
All test execution is isolated inside a Modal.Sandbox that is destroyed
in a try/finally block regardless of outcome.

Modal.Sandbox calls are blocking (synchronous SDK). They are wrapped in
asyncio.to_thread to avoid blocking the FastAPI event loop.

The github_token is read from config["configurable"]["github_token"], never
from state, so it is never checkpointed to PostgreSQL.
"""

import asyncio
import json
import logging
import re
import urllib.parse
from typing import Any

import modal
from langchain_core.runnables import RunnableConfig

from backend.config import settings
from backend.state import PrismState, TestFailure, TestResults
from backend.supabase_client import save_agent_output, update_run_status

logger = logging.getLogger(__name__)

_SANDBOX_TIMEOUT_SECONDS = 120
# Maximum bytes of stdout/stderr stored (tracebacks kept in full; raw output capped)
_MAX_OUTPUT_BYTES = 50_000
_LOCAL_SKIP_REASON = (
    "Test Runner skipped in local development. Repository tests run in "
    "Modal.Sandbox only when the backend is deployed (ENVIRONMENT=production). "
    "This machine uses OPENAI_API_KEY and Supabase keys from .env — not a Modal token."
)


def _use_modal_sandbox() -> bool:
    """Modal sandboxes are production-only. Local uvicorn must not require modal token."""
    return settings.environment == "production"


def _skipped_test_results() -> TestResults:
    return TestResults(
        framework="skipped",
        passed=[],
        failed=[],
        passed_count=0,
        failed_count=0,
        exit_code=0,
        stdout="",
        stderr=_LOCAL_SKIP_REASON,
    )


def _build_clone_url(repo_url: str, token: str) -> str:
    """Inject PAT credentials into the GitHub HTTPS clone URL."""
    parsed = urllib.parse.urlparse(repo_url)
    # Format: https://x-token:<PAT>@github.com/owner/repo.git
    authed = parsed._replace(netloc=f"x-token:{token}@{parsed.netloc}")
    url = urllib.parse.urlunparse(authed)
    if not url.endswith(".git"):
        url += ".git"
    return url


def _detect_framework(file_list: str) -> str:
    """
    Infer the test framework from standard config file names in the output.

    Returns one of: "pytest", "unittest", "jest", "unknown"
    """
    lines = file_list.lower()
    if "pytest.ini" in lines or "conftest.py" in lines:
        return "pytest"
    if "jest.config" in lines or "jest.config.js" in lines or "jest.config.ts" in lines:
        return "jest"
    if "pyproject.toml" in lines or "setup.cfg" in lines or "setup.py" in lines:
        return "pytest"  # assume pytest for Python projects without explicit config
    if "package.json" in lines:
        return "jest"
    return "unknown"


def _build_run_script(framework: str) -> str:
    """Return the shell commands to install dependencies and run the test suite."""
    if framework in ("pytest", "unittest"):
        return (
            "set +e\n"
            "cd /repo\n"
            "export PYTHONPATH=/repo\n"
            "if [ -f requirements.txt ]; then pip install -r requirements.txt -q; fi\n"
            "if [ -f pyproject.toml ]; then pip install -e '.[dev]' -q 2>/dev/null || pip install -e . -q 2>/dev/null || true; fi\n"
            "python -m pytest -p json-report --json-report --json-report-file=/tmp/report.json "
            "--tb=short -q 2>&1\n"
            "echo PRISM_PYTEST_EXIT=$?\n"
            "cat /tmp/report.json 2>/dev/null || echo '{}'\n"
        )
    if framework == "jest":
        return (
            "set -e\n"
            "cd /repo\n"
            "npm install --silent 2>&1\n"
            "npx jest --json --outputFile=/tmp/report.json 2>&1 || true\n"
            "cat /tmp/report.json 2>/dev/null || echo '{}'\n"
        )
    # Unknown — try pytest as the most common Python default
    return (
        "set -e\n"
        "cd /repo\n"
        "if [ -f requirements.txt ]; then pip install -r requirements.txt -q; fi\n"
        "python -m pytest --tb=short -q 2>&1 || true\n"
    )


def _suite_all_passed(
    passed: list[str],
    failed: list[TestFailure],
    exit_code: int,
) -> bool:
    """True only when at least one test ran and none failed."""
    collected = len(passed) + len(failed)
    return collected > 0 and len(failed) == 0 and exit_code == 0


def _pytest_exit_from_stdout(stdout: str, sandbox_exit: int) -> int:
    """Prefer the pytest process code over the wrapper script's last-command exit."""
    for line in stdout.splitlines():
        if line.startswith("PRISM_PYTEST_EXIT="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                break
    return sandbox_exit


def _empty_collection_failure(stdout: str, stderr: str) -> TestFailure:
    log = f"{stdout}\n{stderr}".strip()
    return TestFailure(
        name="(no tests collected)",
        traceback=log[:8_000],
        message=(
            "Test Runner collected 0 tests. That is not a passing suite; "
            "Debugger must not be skipped."
        ),
    )


def _parse_pytest_json(raw_json: str, stdout: str) -> tuple[list[str], list[TestFailure]]:
    """Parse pytest-json-report output into passed/failed lists.

    Falls back to stdout line-parsing when the JSON is invalid OR when the
    parsed report contains no 'tests' array (e.g. pytest-json-report not
    installed and the script emitted '{}').
    """
    passed: list[str] = []
    failed: list[TestFailure] = []
    try:
        data = json.loads(raw_json)
        tests = data.get("tests") or []
        if not tests:
            summary = data.get("summary") or {}
            n_passed = int(summary.get("passed") or 0)
            n_failed = int(summary.get("failed") or 0) + int(summary.get("error") or 0)
            if n_passed or n_failed:
                passed = [f"(passed {i + 1})" for i in range(n_passed)]
                failed = [
                    TestFailure(
                        name=f"(failed {i + 1})",
                        traceback="",
                        message="json-report summary had failures but tests[] was empty.",
                    )
                    for i in range(n_failed)
                ]
                return passed, failed
            raise ValueError("no tests in json report — falling back to stdout")
        for test in tests:
            name = test.get("nodeid", "unknown")
            outcome = test.get("outcome", "")
            if outcome == "passed":
                passed.append(name)
            elif outcome in ("failed", "error"):
                call = test.get("call", {}) or test.get("setup", {}) or {}
                traceback_text = call.get("longrepr", "")
                message = ""
                if isinstance(traceback_text, dict):
                    traceback_text = traceback_text.get("reprcrash", {}).get("message", "")
                failed.append(
                    TestFailure(
                        name=name,
                        traceback=str(traceback_text),
                        message=message or str(traceback_text)[:200],
                    )
                )
    except (json.JSONDecodeError, KeyError, ValueError):
        # Fallback: parse stdout for PASSED/FAILED lines and pytest -q letters
        for line in stdout.splitlines():
            if " PASSED" in line:
                passed.append(line.strip())
            elif " FAILED" in line or " ERROR" in line or line.strip().endswith(" FAILED"):
                failed.append(
                    TestFailure(name=line.strip(), traceback="", message=line.strip())
                )
    return passed, failed


def _parse_jest_json(raw_json: str) -> tuple[list[str], list[TestFailure]]:
    """Parse Jest --json output into passed/failed lists."""
    passed: list[str] = []
    failed: list[TestFailure] = []
    try:
        data = json.loads(raw_json)
        for suite in data.get("testResults", []):
            for test in suite.get("testResults", []):
                full_name = test.get("fullName", test.get("title", "unknown"))
                status = test.get("status", "")
                if status == "passed":
                    passed.append(full_name)
                elif status == "failed":
                    messages = test.get("failureMessages", [])
                    tb = "\n".join(messages)
                    failed.append(TestFailure(name=full_name, traceback=tb, message=tb[:200]))
    except (json.JSONDecodeError, KeyError):
        pass
    return passed, failed


async def test_runner_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    """
    LangGraph node: test_runner.

    Reads: repo_url, run_id
    Config: config["configurable"]["github_token"]
    Writes: test_results, all_tests_passed, current_agent, messages, error
    """
    run_id: str = state["run_id"]
    repo_url: str = state["repo_url"]
    github_token: str = config.get("configurable", {}).get("github_token", "")
    logger.info("[test_runner] Starting — run_id=%s repo=%s", run_id, repo_url)

    try:
        await update_run_status(run_id, "running", "test_runner")
        await save_agent_output(run_id, "test_runner", {}, "start")

        if not _use_modal_sandbox():
            skip_results = _skipped_test_results()
            output_payload: dict[str, Any] = {
                "test_results": dict(skip_results),
                "all_tests_passed": True,
            }
            await save_agent_output(run_id, "test_runner", output_payload, "complete")
            await update_run_status(
                run_id,
                "running",
                "test_runner",
                all_tests_passed=True,
            )
            log_line = "[test_runner] skipped — Modal sandbox is production-only"
            logger.info(log_line)
            return {
                "test_results": skip_results,
                "all_tests_passed": True,
                "current_agent": "test_runner",
                "messages": [log_line],
            }

        clone_url = _build_clone_url(repo_url, github_token)
        # Redact token from any log messages
        safe_url = re.sub(r"x-token:[^@]+@", "x-token:[REDACTED]@", clone_url)
        logger.info("[test_runner] Launching Modal.Sandbox — clone %s", safe_url)

        # ── Build combined clone + detect + test script ────────────────────────
        combined_script = (
            "set -e\n"
            f"git clone --depth=1 {clone_url} /repo 2>&1\n"
            "cd /repo\n"
            "FRAMEWORK=unknown\n"
            "if [ -f pytest.ini ] || [ -f conftest.py ] || [ -f pyproject.toml ] || [ -f setup.cfg ]; then\n"
            "  FRAMEWORK=pytest\n"
            "elif [ -f package.json ]; then\n"
            "  FRAMEWORK=jest\n"
            "fi\n"
            "echo \"PRISM_FRAMEWORK=$FRAMEWORK\"\n"
            "if [ \"$FRAMEWORK\" = 'pytest' ]; then\n"
            "  pip install -q pytest pytest-json-report || true\n"
            "  if [ -f requirements.txt ]; then pip install -q -r requirements.txt 2>/dev/null || true; fi\n"
            "  if [ -f pyproject.toml ]; then pip install -q -e '.[dev]' 2>/dev/null || pip install -q -e . 2>/dev/null || true; fi\n"
            "  export PYTHONPATH=/repo\n"
            "  echo 'PRISM_TREE'\n"
            "  ls -la /repo | head -n 40\n"
            "  ls -la /repo/tests 2>/dev/null || echo 'PRISM_NO_TESTS_DIR'\n"
            "  set +e\n"
            "  python -m pytest -p json-report --json-report --json-report-file=/tmp/report.json "
            "--tb=short -q 2>&1\n"
            "  echo \"PRISM_PYTEST_EXIT=$?\"\n"
            "  set -e\n"
            "  echo 'PRISM_REPORT_START'\n"
            "  cat /tmp/report.json 2>/dev/null || echo '{}'\n"
            "  echo 'PRISM_REPORT_END'\n"
            "elif [ \"$FRAMEWORK\" = 'jest' ]; then\n"
            "  npm install --silent 2>&1 || true\n"
            "  npx jest --json --outputFile=/tmp/report.json 2>&1 || true\n"
            "  echo 'PRISM_REPORT_START'\n"
            "  cat /tmp/report.json 2>/dev/null || echo '{}'\n"
            "  echo 'PRISM_REPORT_END'\n"
            "else\n"
            "  echo 'PRISM_REPORT_START'\n"
            "  echo '{}'\n"
            "  echo 'PRISM_REPORT_END'\n"
            "fi\n"
        )

        sandbox_image = (
            modal.Image.debian_slim(python_version="3.11")
            .apt_install("git", "nodejs", "npm")
            .pip_install("pytest", "pytest-json-report")
        )

        # ── Run Modal.Sandbox in a thread to avoid blocking the event loop ─────
        def _run_sandbox_sync() -> tuple[str, str, int]:
            """All blocking Modal Sandbox operations — runs in a thread pool."""
            # Local uvicorn is outside a Modal container; Sandbox.create requires
            # an App in that case. lookup() also works when already inside Modal.
            sandbox_app = modal.App.lookup("prism-sandbox", create_if_missing=True)
            sb = modal.Sandbox.create(
                "bash",
                "-c",
                combined_script,
                timeout=_SANDBOX_TIMEOUT_SECONDS,
                image=sandbox_image,
                app=sandbox_app,
            )
            try:
                sb.wait()
                raw_stdout = sb.stdout.read() or ""
                raw_stderr = sb.stderr.read() or ""
                exit_code = sb.returncode
                return raw_stdout, raw_stderr, exit_code
            finally:
                try:
                    sb.terminate()
                except Exception as cleanup_exc:
                    logger.warning("[test_runner] Sandbox cleanup failed: %s", cleanup_exc)

        raw_stdout, raw_stderr, exit_code = await asyncio.to_thread(_run_sandbox_sync)
        exit_code = _pytest_exit_from_stdout(raw_stdout, exit_code)

        # ── Detect framework from script output ────────────────────────────────
        framework = "unknown"
        for line in raw_stdout.splitlines():
            if line.startswith("PRISM_FRAMEWORK="):
                framework = line.split("=", 1)[1].strip()
                break
        logger.info("[test_runner] Detected framework: %s exit_code=%d", framework, exit_code)

        logger.info(
            "[test_runner] Tests finished — exit_code=%d stdout_len=%d",
            exit_code,
            len(raw_stdout),
        )

        # ── Parse results ─────────────────────────────────────────────────────
        report_json = ""
        lines = raw_stdout.splitlines()
        in_report = False
        report_lines: list[str] = []
        for line in lines:
            if line.strip() == "PRISM_REPORT_START":
                in_report = True
                continue
            if line.strip() == "PRISM_REPORT_END":
                break
            if in_report:
                report_lines.append(line)
        report_json = "\n".join(report_lines).strip()

        if framework == "jest":
            passed, failed = _parse_jest_json(report_json)
        else:
            passed, failed = _parse_pytest_json(report_json, raw_stdout)

        if not passed and not failed:
            failed = [_empty_collection_failure(raw_stdout, raw_stderr)]

        stdout_stored = raw_stdout[:_MAX_OUTPUT_BYTES]
        stderr_stored = raw_stderr[:_MAX_OUTPUT_BYTES]

        test_results = TestResults(
            framework=framework,
            passed=passed,
            failed=failed,
            passed_count=len(passed),
            failed_count=len(failed),
            exit_code=exit_code,
            stdout=stdout_stored,
            stderr=stderr_stored,
        )
        all_tests_passed = _suite_all_passed(passed, failed, exit_code)

        output_payload: dict[str, Any] = {
            "test_results": dict(test_results),
            "all_tests_passed": all_tests_passed,
        }
        await save_agent_output(run_id, "test_runner", output_payload, "complete")
        await update_run_status(
            run_id,
            "running",
            "test_runner",
            all_tests_passed=all_tests_passed,
        )

        log_line = (
            f"[test_runner] {framework}: {len(passed)} passed, {len(failed)} failed "
            f"(exit {exit_code})"
        )
        logger.info(log_line)

        return {
            "test_results": test_results,
            "all_tests_passed": all_tests_passed,
            "current_agent": "test_runner",
            "messages": [log_line],
        }

    except RuntimeError as exc:
        msg = f"[test_runner] Failed: {exc}"
        logger.error(msg, exc_info=True)
        empty_results = TestResults(
            framework="unknown",
            passed=[],
            failed=[],
            passed_count=0,
            failed_count=0,
            exit_code=1,
            stdout="",
            stderr=str(exc),
        )
        await save_agent_output(
            run_id,
            "test_runner",
            {
                "error": str(exc),
                "test_results": dict(empty_results),
                "all_tests_passed": False,
            },
            "complete",
        )
        await update_run_status(run_id, "running", "test_runner", all_tests_passed=False)
        return {
            "test_results": empty_results,
            "all_tests_passed": False,
            "error": str(exc),
            "current_agent": "test_runner",
            "messages": [msg],
        }
    except Exception as exc:
        msg = f"[test_runner] Unexpected error: {exc}"
        logger.error(msg, exc_info=True)
        empty_results = TestResults(
            framework="unknown",
            passed=[],
            failed=[],
            passed_count=0,
            failed_count=0,
            exit_code=1,
            stdout="",
            stderr=str(exc),
        )
        await save_agent_output(
            run_id,
            "test_runner",
            {
                "error": str(exc),
                "test_results": dict(empty_results),
                "all_tests_passed": False,
            },
            "complete",
        )
        await update_run_status(run_id, "running", "test_runner", all_tests_passed=False)
        return {
            "test_results": empty_results,
            "all_tests_passed": False,
            "error": str(exc),
            "current_agent": "test_runner",
            "messages": [msg],
        }
