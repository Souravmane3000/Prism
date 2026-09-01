"""
backend/test_outcome.py — Shared classification of TestResults for Debug UI.

Frontend `lib/output.ts` `classifyTestResults` must stay in lockstep with this
module. Contract tests in tests/unit/test_frontend_backend_contract.py enforce it.

Outcomes:
  missing     — no test_results yet
  skipped     — local development; Modal sandbox not used
  did_not_run — sandbox/process failed before any tests were collected
  failed      — one or more tests failed, or suite exited non-zero after collection
  passed      — tests ran and none failed
"""

from typing import Any, Optional

SKIPPED_FRAMEWORKS = frozenset({"skipped", "not_run"})


def classify_test_results(results: Optional[dict[str, Any]]) -> str:
    """Classify a TestResults-like dict into a Debug-tab outcome."""
    if not results:
        return "missing"

    framework = str(results.get("framework") or "").lower()
    if framework in SKIPPED_FRAMEWORKS:
        return "skipped"

    passed = int(results.get("passed_count") or 0)
    failed_count = int(results.get("failed_count") or 0)
    failed_list = results.get("failed") or []
    exit_code = int(results.get("exit_code") or 0)
    collected = passed + failed_count > 0 or (
        isinstance(failed_list, list) and len(failed_list) > 0
    )

    # Unknown framework + 0 collected is an empty sandbox dump, not a green suite.
    if not collected and framework in {"unknown", ""}:
        return "did_not_run"
    if exit_code != 0 and not collected:
        return "did_not_run"
    if failed_count > 0 or (isinstance(failed_list, list) and len(failed_list) > 0):
        return "failed"
    if exit_code != 0:
        return "failed"
    return "passed"
