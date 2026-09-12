"""Path safety for generated patches. Never allow .git or escapes."""

from __future__ import annotations


class UnsafePathError(ValueError):
    reason = "unsafe_path"


def normalize_repo_path(path: str) -> str:
    cleaned = (path or "").replace("\\", "/").strip()
    if not cleaned:
        raise UnsafePathError("Empty path is not allowed")
    if cleaned.startswith("/") or cleaned.startswith("~"):
        raise UnsafePathError(f"Absolute path is not allowed: {path}")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise UnsafePathError(f"Path escapes repository root: {path}")
    if parts and parts[0] == ".git":
        raise UnsafePathError("Patch cannot modify .git")
    if any(part == ".git" for part in parts):
        raise UnsafePathError("Patch cannot modify .git")
    return "/".join(parts)
