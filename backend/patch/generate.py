"""Turn a ModificationSpec into a StructuredPatch."""

from __future__ import annotations

from backend.patch.apply_spec import (
    EmptyPatchError,
    UnsupportedModificationError,
    apply_spec,
)
from backend.patch.render import render_unified_diff
from backend.patch.spec import ModificationSpec
from backend.patch.structured import build_structured_patch
from backend.state import PatchFileOperation, StructuredPatch
from backend.workspace import LocalGitWorkspace, WorkspaceError


def originals_for_spec(
    spec: ModificationSpec,
    workspace: LocalGitWorkspace,
) -> dict[str, str | None]:
    originals: dict[str, str | None] = {}
    for change in spec.files:
        paths = [change.path]
        if change.operation == "rename" and change.old_path:
            paths.append(change.old_path)
        for path in paths:
            content = workspace.read_file(path)
            if content is None and workspace.file_exists(path):
                raise UnsupportedModificationError(
                    f"Unsupported or binary file cannot be patched: {path}"
                )
            originals[path] = content
    return originals


def generate_structured_patch(
    spec: ModificationSpec,
    workspace: LocalGitWorkspace,
    *,
    baseline_sha: str,
) -> StructuredPatch:
    """Diff spec originals vs new contents (clean baseline workspaces)."""
    if workspace.baseline_sha != baseline_sha:
        raise WorkspaceError("Patch baseline does not match inspection workspace HEAD")
    if not spec.files:
        raise EmptyPatchError("Approved implementation produced no file operations")
    originals = originals_for_spec(spec, workspace)
    changes = apply_spec(spec, originals)
    unified_diff, operations = render_unified_diff(changes)
    return build_structured_patch(
        baseline_sha=baseline_sha,
        unified_diff=unified_diff,
        modified_files=operations,
    )


def _operations_from_name_status(raw: str) -> list[PatchFileOperation]:
    operations: list[PatchFileOperation] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        code, _, rest = line.partition("\t")
        code = code.strip()
        if code.startswith("A"):
            operations.append({"path": rest.strip(), "operation": "add", "old_path": None})
        elif code.startswith("D"):
            operations.append({"path": rest.strip(), "operation": "delete", "old_path": None})
        elif code.startswith("R"):
            old, _, new = rest.partition("\t")
            operations.append({"path": new.strip(), "operation": "rename", "old_path": old.strip()})
        else:
            operations.append({"path": rest.strip(), "operation": "modify", "old_path": None})
    return operations


def generate_cumulative_structured_patch(
    spec: ModificationSpec,
    workspace: LocalGitWorkspace,
    *,
    baseline_sha: str,
) -> StructuredPatch:
    """Apply a focused spec onto the current tree, then diff against baseline_sha.

    Used by Debugger Fix Patch so current_patch always applies to a clean
    baseline clone (original implementation + prior fixes).
    """
    if workspace.head_sha() != baseline_sha:
        raise WorkspaceError("Cumulative patch generation requires HEAD at baseline_sha")
    originals = originals_for_spec(spec, workspace)
    changes = apply_spec(spec, originals)
    for path, (_old, new) in changes.items():
        if new is None:
            workspace.delete_file(path)
        else:
            workspace.write_file(path, new)
    workspace.stage_all()
    unified = workspace.diff_against(baseline_sha)
    if not unified.strip():
        raise EmptyPatchError("Fix produced no resulting diff against baseline")
    operations = _operations_from_name_status(workspace.name_status_against(baseline_sha))
    return build_structured_patch(
        baseline_sha=baseline_sha,
        unified_diff=unified if unified.endswith("\n") else unified + "\n",
        modified_files=operations,
    )
