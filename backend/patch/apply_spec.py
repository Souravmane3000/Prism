"""Apply a ModificationSpec to in-memory file contents."""

from __future__ import annotations

from backend.patch.paths import UnsafePathError, normalize_repo_path
from backend.patch.spec import FileChange, ModificationSpec


class EmptyPatchError(ValueError):
    reason = "empty_patch"


class UnsupportedModificationError(ValueError):
    reason = "unsupported_modification"


def apply_spec(
    spec: ModificationSpec,
    originals: dict[str, str | None],
) -> dict[str, tuple[str | None, str | None]]:
    """Return mapping path -> (old_content, new_content). new None means delete."""
    changes: dict[str, tuple[str | None, str | None]] = {}
    if not spec.files:
        raise EmptyPatchError("Approved implementation produced no file operations")
    for change in spec.files:
        path = normalize_repo_path(change.path)
        old_path = normalize_repo_path(change.old_path) if change.old_path else None
        if change.operation == "add":
            content = change.new_content
            if content is None:
                raise UnsupportedModificationError(f"add requires new_content: {path}")
            changes[path] = (None, content)
        elif change.operation == "delete":
            changes[path] = (originals.get(path), None)
        elif change.operation == "rename":
            if not old_path:
                raise UnsupportedModificationError(f"rename requires old_path: {path}")
            source = originals.get(old_path)
            new_content = change.new_content if change.new_content is not None else source
            changes[old_path] = (source, None)
            changes[path] = (None, new_content)
        else:
            original = originals.get(path)
            if original is None:
                raise UnsupportedModificationError(f"modify target missing: {path}")
            changes[path] = (original, _apply_replacements(original, change))
    if all(old == new for old, new in changes.values()):
        raise EmptyPatchError("Approved implementation produced no file operations")
    return changes


def _apply_replacements(original: str, change: FileChange) -> str:
    if change.new_content is not None and not change.replacements:
        return change.new_content
    updated = original
    for replacement in change.replacements:
        if replacement.old not in updated:
            raise UnsupportedModificationError(
                f"replacement old text not found in {change.path}"
            )
        updated = updated.replace(replacement.old, replacement.new, 1)
    return updated
