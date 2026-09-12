"""Rollback to baseline_sha. Does not set FAILED_MAX_VERIFICATION."""

from __future__ import annotations

from dataclasses import dataclass

from backend.patch.repository import inspect_repository_state, require_baseline_sha
from backend.workspace import LocalGitWorkspace


class RollbackError(RuntimeError):
    reason = "rollback_failed"

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason


@dataclass(frozen=True)
class RollbackResult:
    head_sha: str
    working_tree_clean: bool


def rollback_to_baseline(
    workspace: LocalGitWorkspace,
    *,
    baseline_sha: str | None,
) -> RollbackResult:
    expected = require_baseline_sha(baseline_sha)
    if workspace.baseline_sha != expected:
        raise RollbackError(
            "Workspace baseline does not match session baseline_sha",
            reason="stale_repository_state",
        )
    workspace.reset_to_baseline()
    head = workspace.head_sha()
    inspection = inspect_repository_state(workspace.root, baseline_sha=expected)
    if head != expected or inspection["head_sha"] != expected:
        raise RollbackError(
            "Rollback verification failed: HEAD != baseline_sha",
            reason="rollback_verification_failed",
        )
    if not inspection["working_tree_clean"]:
        raise RollbackError(
            "Rollback verification failed: working tree is dirty",
            reason="rollback_verification_failed",
        )
    return RollbackResult(head_sha=head, working_tree_clean=True)
