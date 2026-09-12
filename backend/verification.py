"""Verification attempt contract and PASS/FAIL routing helpers (Phase 4).

A verification attempt is one complete Test Runner execution against the
current applied repository state. Debugger, Fix Patch, and Apply Patch
are not verification attempts. hitl_revision_count is independent.
"""

from __future__ import annotations

from typing import Final, Literal

MAX_VERIFICATION_ATTEMPTS: Final = 3
MIN_DEBUG_CONFIDENCE: Final = 0.7

FailureCategory = Literal[
    "assertion",
    "compile",
    "import",
    "command_not_found",
    "timeout",
    "sandbox",
    "empty_collection",
    "unknown",
]

CODE_FAILURE_CATEGORIES: Final = frozenset({"assertion", "compile", "import", "unknown"})
ENVIRONMENT_FAILURE_CATEGORIES: Final = frozenset(
    {"command_not_found", "timeout", "sandbox"}
)


class VerificationAttemptError(ValueError):
    """Raised when a fourth Test Runner execution would be started."""

    reason = "max_verification_attempts"


def increment_verification_attempts(current: int | None) -> int:
    """Increment immediately before a Test Runner execution.

    Starts at 0; the first run becomes 1. Refuses a value above 3.
    """
    value = 0 if current is None else int(current)
    if value < 0:
        value = 0
    nxt = value + 1
    if nxt > MAX_VERIFICATION_ATTEMPTS:
        raise VerificationAttemptError(
            f"verification_attempts cannot exceed {MAX_VERIFICATION_ATTEMPTS}"
        )
    return nxt


def suite_all_passed(
    passed: list[str],
    failed: list[object],
    exit_code: int,
    *,
    framework: str | None = None,
) -> bool:
    """PASS only when at least one test ran, none failed, and exit_code == 0.

    ``framework='skipped'`` is never a pass — verification was not performed.
    """
    if (framework or "").lower() in {"skipped", "not_run"}:
        return False
    collected = len(passed) + len(failed)
    return collected > 0 and len(failed) == 0 and exit_code == 0


def classify_failure_category(
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    failed: list[object],
    framework: str | None = None,
    timed_out: bool = False,
) -> str:
    combined = f"{stdout}\n{stderr}".lower()
    if timed_out or "timed out" in combined:
        return "timeout"
    if "command not found" in combined or (
        "no such file or directory" in combined
        and ("pytest" in combined or "jest" in combined)
    ):
        return "command_not_found"
    if "modulenotfounderror" in combined or "importerror" in combined:
        return "import"
    if "syntaxerror" in combined or "cannot compile" in combined:
        return "compile"
    if not failed and exit_code != 0:
        if "sandbox" in combined or "clone" in combined:
            return "sandbox"
        return "sandbox"
    if failed:
        name = ""
        first = failed[0]
        if isinstance(first, dict):
            name = str(first.get("name") or "")
        if name == "(no tests collected)":
            return "empty_collection"
        return "assertion"
    return "unknown"


def is_environment_failure(category: str | None) -> bool:
    return (category or "") in ENVIRONMENT_FAILURE_CATEGORIES


def is_code_failure(category: str | None) -> bool:
    if not category:
        return True
    return category in CODE_FAILURE_CATEGORIES or category == "empty_collection"


def should_debug(
    *,
    all_tests_passed: bool | None,
    verification_attempts: int,
    failure_category: str | None,
) -> bool:
    """True when FAIL has attempts remaining and the failure is code-level."""
    if all_tests_passed is True:
        return False
    if verification_attempts >= MAX_VERIFICATION_ATTEMPTS:
        return False
    if is_environment_failure(failure_category):
        return False
    return True


def should_rollback(
    *,
    all_tests_passed: bool | None,
    verification_attempts: int,
    failure_category: str | None,
) -> bool:
    """True after the third failed verification attempt (code or empty suite)."""
    if all_tests_passed is True:
        return False
    if verification_attempts < MAX_VERIFICATION_ATTEMPTS:
        return False
    if is_environment_failure(failure_category):
        return False
    return True


def route_after_verification(
    *,
    all_tests_passed: bool | None,
    verification_attempts: int,
    failure_category: str | None,
) -> Literal["pr_summarizer", "debugger", "rollback", "end"]:
    """Deterministic Test Runner routing. PASS never enters Debugger."""
    if all_tests_passed is True:
        return "pr_summarizer"
    if is_environment_failure(failure_category):
        return "end"
    if verification_attempts < MAX_VERIFICATION_ATTEMPTS:
        return "debugger"
    return "rollback"
