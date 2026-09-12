"""Patch application preconditions and Git apply.

Does not commit, push, or open a PR. Does not mark VERIFIED.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from backend.patch.paths import UnsafePathError, normalize_repo_path
from backend.patch.repository import (
    MissingBaselineError,
    RepositoryInspection,
    RepositoryMismatchError,
    inspect_repository_state,
    require_baseline_sha,
)
from backend.state import PatchFileOperation, StructuredPatch
from backend.workspace import LocalGitWorkspace, WorkspaceError

logger = logging.getLogger(__name__)

# APPROVED: human-approved initial patch (Phase 5).
# VALIDATED: debugger-origin recovery patches only (Phase 4 bypass).
APPLY_ALLOWED_STATUSES = frozenset({"VALIDATED", "APPROVED"})
DUPLICATE_APPLY_STATUS = "APPLIED"
_BLOCKED_STATUSES = frozenset(
    {
        "GENERATED",
        "INVALID",
        "REJECTED",
        "REVIEWED",
        "VERIFIED",
        "ROLLED_BACK",
    }
)


class ApplyError(RuntimeError):
    """Structured apply failure. Does not imply the patch schema is INVALID."""

    reason = "patch_application_failed"

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason


@dataclass(frozen=True)
class ApplyResult:
    patch_status: Literal["APPLIED"]
    final_diff: str
    modified_files: list[PatchFileOperation]
    head_sha: str
    working_tree_clean: bool
    inspection: RepositoryInspection


def materialize_patch(workspace: LocalGitWorkspace, patch: StructuredPatch) -> None:
    """Apply unified_diff into a clean baseline workspace. No lifecycle checks."""
    diff = patch["unified_diff"]
    workspace.apply_check(diff)
    workspace.apply_diff(diff)


def check_apply_preconditions(
    *,
    patch: StructuredPatch | None,
    patch_status: str | None,
    baseline_sha: str | None,
    workspace: LocalGitWorkspace | None = None,
    patch_origin: str | None = None,
) -> StructuredPatch:
    if patch_status == DUPLICATE_APPLY_STATUS:
        raise ApplyError("Patch is already APPLIED", reason="duplicate_apply")
    if patch_status == "APPROVED":
        pass
    elif patch_status == "VALIDATED":
        # Phase 4/5 lock: only debugger-origin patches may apply at VALIDATED.
        if patch_origin != "debugger":
            raise ApplyError(
                "VALIDATED code_modifier patches require Diff Reviewer + HITL #3 APPROVED",
                reason="invalid_lifecycle",
            )
    elif patch_status in _BLOCKED_STATUSES or patch_status not in APPLY_ALLOWED_STATUSES:
        raise ApplyError(
            f"Patch status {patch_status!r} cannot be applied",
            reason="invalid_lifecycle",
        )
    else:
        raise ApplyError(
            f"Patch status {patch_status!r} cannot be applied",
            reason="invalid_lifecycle",
        )
    if not patch:
        raise ApplyError("current_patch is required", reason="missing_patch")
    try:
        expected = require_baseline_sha(baseline_sha)
    except MissingBaselineError as exc:
        raise ApplyError(str(exc), reason=exc.reason) from exc
    patch_baseline = patch.get("baseline_sha")
    if not patch_baseline or patch_baseline != expected:
        raise ApplyError(
            "Patch baseline_sha does not match session baseline",
            reason="stale_repository_state",
        )
    if workspace is not None:
        head = workspace.head_sha()
        if head != expected or workspace.baseline_sha != expected:
            raise ApplyError(
                "Workspace HEAD does not match session baseline_sha",
                reason="stale_repository_state",
            )
        try:
            inspection = inspect_repository_state(
                workspace.root,
                baseline_sha=expected,
                require_baseline=True,
            )
        except RepositoryMismatchError as exc:
            raise ApplyError(str(exc), reason="stale_repository_state") from exc
        if not inspection["working_tree_clean"]:
            raise ApplyError(
                "Working tree must be clean before apply",
                reason="dirty_working_tree",
            )
    return patch


def apply_patch(
    *,
    patch: StructuredPatch | None,
    patch_status: str | None,
    baseline_sha: str | None,
    workspace: LocalGitWorkspace,
    patch_origin: str | None = None,
) -> ApplyResult:
    checked = check_apply_preconditions(
        patch=patch,
        patch_status=patch_status,
        baseline_sha=baseline_sha,
        workspace=workspace,
        patch_origin=patch_origin,
    )
    expected = require_baseline_sha(baseline_sha)
    try:
        materialize_patch(workspace, checked)
        workspace.stage_all()
        return _post_apply_state(workspace, expected, checked)
    except ApplyError:
        _recover_workspace(workspace)
        raise
    except (WorkspaceError, UnsafePathError) as exc:
        _recover_workspace(workspace)
        raise ApplyError(str(exc), reason=getattr(exc, "reason", "apply_failed")) from exc
    except Exception as exc:
        _recover_workspace(workspace)
        raise ApplyError(
            f"Patch application failed: {type(exc).__name__}",
            reason="apply_failed",
        ) from exc


def _operations_from_name_status(raw: str) -> list[PatchFileOperation]:
    operations: list[PatchFileOperation] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        code, _, rest = line.partition("\t")
        path = rest.strip()
        try:
            normalize_repo_path(path.split("\t")[-1])
        except UnsafePathError as exc:
            raise ApplyError(str(exc), reason="unsafe_path") from exc
        if code.startswith("A"):
            operations.append({"path": path, "operation": "add", "old_path": None})
        elif code.startswith("D"):
            operations.append({"path": path, "operation": "delete", "old_path": None})
        else:
            operations.append({"path": path, "operation": "modify", "old_path": None})
    return operations


def _post_apply_state(
    workspace: LocalGitWorkspace,
    expected: str,
    patch: StructuredPatch,
) -> ApplyResult:
    head = workspace.head_sha()
    if head != expected:
        raise ApplyError(
            "Apply must not create a commit or move HEAD",
            reason="unexpected_commit",
        )
    final_diff = workspace.diff_against(expected)
    if not final_diff.strip():
        raise ApplyError("Apply produced no resulting diff", reason="empty_result")
    modified_files = _operations_from_name_status(workspace.name_status_against(expected))
    inspection = inspect_repository_state(workspace.root, baseline_sha=expected)
    if inspection["working_tree_clean"]:
        raise ApplyError("Working tree is unexpectedly clean after apply", reason="empty_result")
    return ApplyResult(
        patch_status="APPLIED",
        final_diff=final_diff if final_diff.endswith("\n") else final_diff + "\n",
        modified_files=modified_files or list(patch.get("modified_files") or []),
        head_sha=head,
        working_tree_clean=False,
        inspection=inspection,
    )


def _recover_workspace(workspace: LocalGitWorkspace) -> None:
    try:
        workspace.reset_to_baseline()
    except Exception:
        logger.warning("Failed to recover workspace after apply error", exc_info=True)
