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
import os
import re
import subprocess
import urllib.parse
from typing import Any

import modal
from langchain_core.runnables import RunnableConfig

from backend.config import settings
from backend.events import (
    VERIFICATION_COMPLETED,
    VERIFICATION_STARTED,
    emit_error_event,
    emit_event,
    sanitize_log_text,
)
from backend.sandbox import reconstruct_applied_workspace
from backend.state import PrismState, TestFailure, TestResults
from backend.supabase_client import update_run_status
from backend.verification import (
    VerificationAttemptError,
    classify_failure_category,
    increment_verification_attempts,
    is_environment_failure,
    suite_all_passed,
)
from backend.workspace import LocalGitWorkspace

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
    framework: str | None = None,
) -> bool:
    """True only when at least one test ran and none failed."""
    return suite_all_passed(passed, failed, exit_code, framework=framework)


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


def _classify_from_output(
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    failed: list[TestFailure],
    framework: str,
    timed_out: bool = False,
) -> str:
    return classify_failure_category(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        failed=failed,
        framework=framework,
        timed_out=timed_out,
    )


def _run_pytest_local(workspace: LocalGitWorkspace) -> tuple[str, str, int, str, int]:
    """Run pytest in an isolated workspace via argv (no shell interpolation)."""
    import sys
    import time

    command = [sys.executable, "-m", "pytest", "--tb=short", "-q"]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=workspace.root,
            capture_output=True,
            text=True,
            timeout=_SANDBOX_TIMEOUT_SECONDS,
            shell=False,
            env={**os.environ, "PYTHONPATH": workspace.root},
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        return stdout, stderr, int(completed.returncode), "python -m pytest --tb=short -q", duration_ms
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + "\npytest timed out"
        return str(stdout), str(stderr), 124, "python -m pytest --tb=short -q", duration_ms


def _empty_results(
    *,
    exit_code: int,
    stderr: str,
    category: str,
    command: str = "python -m pytest",
) -> TestResults:
    return TestResults(
        framework="unknown",
        passed=[],
        failed=[],
        passed_count=0,
        failed_count=0,
        exit_code=exit_code,
        stdout="",
        stderr=stderr[:_MAX_OUTPUT_BYTES],
        command=command,
        truncated=len(stderr) > _MAX_OUTPUT_BYTES,
        failure_category=category,
    )


async def test_runner_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    """Run real tests against the applied workspace. Increments verification_attempts."""
    run_id: str = state["run_id"]
    repo_url: str = state["repo_url"]
    github_token: str = config.get("configurable", {}).get("github_token", "")
    workspace: LocalGitWorkspace | None = None
    logger.info("[test_runner] Starting — run_id=%s repo=%s", run_id, repo_url)

    try:
        attempts = increment_verification_attempts(state.get("verification_attempts"))
    except VerificationAttemptError as exc:
        await emit_error_event(run_id, "test_runner", exc)
        await update_run_status(run_id, "failed", "test_runner", error=str(exc))
        return {
            "all_tests_passed": False,
            "error": str(exc),
            "session_status": "PATCH_ERROR",
            "current_agent": "test_runner",
            "messages": [f"[test_runner] {exc}"],
        }

    try:
        await update_run_status(run_id, "running", "test_runner")
        await emit_event(
            run_id,
            VERIFICATION_STARTED,
            {
                "summary": f"Verification attempt {attempts}",
                "verification_attempts": attempts,
            },
        )

        patch = state.get("current_patch")
        baseline = state.get("baseline_sha")
        if not patch or not baseline:
            results = _empty_results(
                exit_code=1,
                stderr="Test Runner requires an applied current_patch at baseline_sha",
                category="sandbox",
            )
            await emit_event(
                run_id,
                VERIFICATION_COMPLETED,
                {
                    "summary": f"Verification attempt {attempts} — environment failure",
                    "verification_attempts": attempts,
                    "all_tests_passed": False,
                    "failure_category": "sandbox",
                    "passed_count": 0,
                    "failed_count": 0,
                },
            )
            return {
                "verification_attempts": attempts,
                "test_results": results,
                "all_tests_passed": False,
                "error": results["stderr"],
                "current_agent": "test_runner",
                "messages": ["[test_runner] missing applied patch"],
            }

        if _use_modal_sandbox():
            raw_stdout, raw_stderr, exit_code, timed_out = await _run_modal(
                repo_url, github_token, baseline, patch["unified_diff"]
            )
            command = "python -m pytest (modal sandbox)"
            duration_ms = None
        else:
            workspace = reconstruct_applied_workspace(repo_url, github_token, baseline, patch)
            raw_stdout, raw_stderr, exit_code, command, duration_ms = _run_pytest_local(workspace)
            timed_out = exit_code == 124

        exit_code = _pytest_exit_from_stdout(raw_stdout, exit_code)
        framework = "pytest"
        for line in raw_stdout.splitlines():
            if line.startswith("PRISM_FRAMEWORK="):
                framework = line.split("=", 1)[1].strip()
                break

        report_json = ""
        in_report = False
        report_lines: list[str] = []
        for line in raw_stdout.splitlines():
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
            passed, failed = _parse_pytest_json(report_json or "{}", raw_stdout)

        if not passed and not failed:
            failed = [_empty_collection_failure(raw_stdout, raw_stderr)]

        truncated = len(raw_stdout) > _MAX_OUTPUT_BYTES or len(raw_stderr) > _MAX_OUTPUT_BYTES
        stdout_stored = sanitize_log_text(raw_stdout[:_MAX_OUTPUT_BYTES], limit=_MAX_OUTPUT_BYTES)
        stderr_stored = sanitize_log_text(raw_stderr[:_MAX_OUTPUT_BYTES], limit=_MAX_OUTPUT_BYTES)
        category = _classify_from_output(
            exit_code=exit_code,
            stdout=raw_stdout,
            stderr=raw_stderr,
            failed=failed,
            framework=framework,
            timed_out=timed_out,
        )
        all_tests_passed = _suite_all_passed(passed, failed, exit_code, framework)
        if all_tests_passed:
            category = "unknown"
        test_results = TestResults(
            framework=framework,
            passed=passed,
            failed=failed,
            passed_count=len(passed),
            failed_count=len(failed),
            exit_code=exit_code,
            stdout=stdout_stored,
            stderr=stderr_stored,
            command=command,
            truncated=truncated,
        )
        if duration_ms is not None:
            test_results["duration_ms"] = duration_ms
        if not all_tests_passed:
            test_results["failure_category"] = category
        await emit_event(
            run_id,
            VERIFICATION_COMPLETED,
            {
                "summary": (
                    f"Verification attempt {attempts} — "
                    + ("tests passed" if all_tests_passed else "tests failed")
                ),
                "verification_attempts": attempts,
                "all_tests_passed": all_tests_passed,
                "failure_category": None if all_tests_passed else category,
                "passed_count": len(passed),
                "failed_count": len(failed),
            },
        )
        await update_run_status(
            run_id,
            "failed" if (not all_tests_passed and is_environment_failure(category)) else "running",
            "test_runner",
            error=None if all_tests_passed or not is_environment_failure(category) else category,
            all_tests_passed=all_tests_passed,
        )
        patch_status = "VERIFIED" if all_tests_passed else state.get("patch_status")
        log_line = (
            f"[test_runner] attempt {attempts} {framework}: "
            f"{len(passed)} passed, {len(failed)} failed (exit {exit_code})"
        )
        result: dict[str, Any] = {
            "verification_attempts": attempts,
            "test_results": test_results,
            "all_tests_passed": all_tests_passed,
            "current_agent": "test_runner",
            "messages": [log_line],
            "error": None,
        }
        if patch_status:
            result["patch_status"] = patch_status
        if all_tests_passed:
            result["session_status"] = "RUNNING"
        return result

    except Exception as exc:
        msg = f"[test_runner] Failed: {exc}"
        logger.error(msg, exc_info=True)
        category = "timeout" if "timeout" in str(exc).lower() else "sandbox"
        empty_results = _empty_results(exit_code=1, stderr=str(exc), category=category)
        await emit_error_event(run_id, "test_runner", exc)
        await emit_event(
            run_id,
            VERIFICATION_COMPLETED,
            {
                "summary": f"Verification attempt {attempts} — execution error",
                "verification_attempts": attempts,
                "all_tests_passed": False,
                "failure_category": category,
                "passed_count": 0,
                "failed_count": 0,
            },
        )
        await update_run_status(run_id, "running", "test_runner", all_tests_passed=False)
        return {
            "verification_attempts": attempts,
            "test_results": empty_results,
            "all_tests_passed": False,
            "error": str(exc),
            "current_agent": "test_runner",
            "messages": [msg],
        }
    finally:
        if workspace is not None:
            workspace.close()


async def _run_modal(
    repo_url: str,
    token: str,
    baseline_sha: str,
    unified_diff: str,
) -> tuple[str, str, int, bool]:
    import base64

    clone_url = _build_clone_url(repo_url, token)
    encoded = base64.b64encode(unified_diff.encode("utf-8")).decode("ascii")
    combined_script = (
        "set -e\n"
        f"git clone {clone_url} /repo 2>&1\n"
        "cd /repo\n"
        f"git checkout --detach {baseline_sha}\n"
        f"python -c \"import base64,pathlib; pathlib.Path('/tmp/prism.patch').write_bytes(base64.b64decode('{encoded}'))\"\n"
        "git apply /tmp/prism.patch\n"
        "pip install -q pytest pytest-json-report || true\n"
        "export PYTHONPATH=/repo\n"
        "echo PRISM_FRAMEWORK=pytest\n"
        "set +e\n"
        "python -m pytest -p json-report --json-report --json-report-file=/tmp/report.json --tb=short -q 2>&1\n"
        "echo PRISM_PYTEST_EXIT=$?\n"
        "echo PRISM_REPORT_START\n"
        "cat /tmp/report.json 2>/dev/null || echo '{}'\n"
        "echo PRISM_REPORT_END\n"
    )
    sandbox_image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("git")
        .pip_install("pytest", "pytest-json-report")
    )

    def _run_sandbox_sync() -> tuple[str, str, int]:
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
            return sb.stdout.read() or "", sb.stderr.read() or "", sb.returncode if sb.returncode is not None else -1
        finally:
            try:
                sb.terminate()
            except Exception as cleanup_exc:
                logger.warning("[test_runner] Sandbox cleanup failed: %s", cleanup_exc)

    stdout, stderr, code = await asyncio.to_thread(_run_sandbox_sync)
    return stdout, stderr, code, False
