"""Deterministic StructuredPatch validation."""

from __future__ import annotations

from dataclasses import dataclass

from backend.patch.paths import UnsafePathError, normalize_repo_path
from backend.patch.structured import checksum_sha256
from backend.state import StructuredPatch
from backend.workspace import LocalGitWorkspace, WorkspaceError


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    patch_status: str
    reason: str | None = None


def validate_structured_patch(
    patch: StructuredPatch | None,
    *,
    expected_baseline: str | None,
    workspace: LocalGitWorkspace | None = None,
) -> ValidationResult:
    if not patch:
        return ValidationResult(False, "INVALID", "missing_patch")
    if patch.get("schema_version") != "1":
        return ValidationResult(False, "INVALID", "schema_version")
    if not patch.get("patch_id"):
        return ValidationResult(False, "INVALID", "missing_patch_id")
    baseline = patch.get("baseline_sha")
    if not baseline or (expected_baseline and baseline != expected_baseline):
        return ValidationResult(False, "INVALID", "stale_repository_state")
    diff = patch.get("unified_diff") or ""
    if not str(diff).strip():
        return ValidationResult(False, "INVALID", "empty_patch")
    if checksum_sha256(diff if diff.endswith("\n") else diff + "\n") != patch.get(
        "checksum_sha256"
    ) and checksum_sha256(diff) != patch.get("checksum_sha256"):
        return ValidationResult(False, "INVALID", "checksum_mismatch")
    files = patch.get("modified_files") or []
    if not files:
        return ValidationResult(False, "INVALID", "empty_patch")
    try:
        for item in files:
            normalize_repo_path(item["path"])
            if item.get("old_path"):
                normalize_repo_path(item["old_path"])
    except UnsafePathError:
        return ValidationResult(False, "INVALID", "unsafe_path")
    if workspace is not None:
        try:
            workspace.apply_check(diff)
        except (WorkspaceError, UnsafePathError):
            return ValidationResult(False, "INVALID", "git_apply_check_failed")
    return ValidationResult(True, "VALIDATED")
