"""Render git-compatible unified diffs from old/new file contents."""

from __future__ import annotations

import difflib
from collections.abc import Iterable

from backend.patch.apply_spec import EmptyPatchError
from backend.state import PatchFileOperation


def _ensure_trailing_newline(text: str) -> str:
    if text == "":
        return text
    return text if text.endswith("\n") else text + "\n"


def _join_diff(lines: Iterable[str]) -> str:
    parts: list[str] = []
    for line in lines:
        parts.append(line if line.endswith("\n") else line + "\n")
    return "".join(parts)


def _file_diff(path: str, old: str | None, new: str | None) -> str:
    if old is None and new is None:
        return ""
    if old is None:
        new_lines = _ensure_trailing_newline(new or "").splitlines()
        body = _join_diff(
            difflib.unified_diff(
                [],
                new_lines,
                fromfile="/dev/null",
                tofile=f"b/{path}",
                lineterm="\n",
            )
        )
        return (
            f"diff --git a/{path} b/{path}\n"
            "new file mode 100644\n"
            + body
        )
    if new is None:
        old_lines = _ensure_trailing_newline(old).splitlines()
        body = _join_diff(
            difflib.unified_diff(
                old_lines,
                [],
                fromfile=f"a/{path}",
                tofile="/dev/null",
                lineterm="\n",
            )
        )
        return (
            f"diff --git a/{path} b/{path}\n"
            "deleted file mode 100644\n"
            + body
        )
    old_text = _ensure_trailing_newline(old)
    new_text = _ensure_trailing_newline(new)
    if old_text == new_text:
        return ""
    body = _join_diff(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )
    if not body:
        return ""
    return f"diff --git a/{path} b/{path}\n" + body


def render_unified_diff(
    changes: dict[str, tuple[str | None, str | None]],
) -> tuple[str, list[PatchFileOperation]]:
    chunks: list[str] = []
    operations: list[PatchFileOperation] = []
    for path in sorted(changes):
        old, new = changes[path]
        chunk = _file_diff(path, old, new)
        if not chunk:
            continue
        chunks.append(chunk)
        if old is None:
            op: PatchFileOperation = {"path": path, "operation": "add", "old_path": None}
        elif new is None:
            op = {"path": path, "operation": "delete", "old_path": None}
        else:
            op = {"path": path, "operation": "modify", "old_path": None}
        operations.append(op)
    unified = "".join(chunks)
    if not unified.strip():
        raise EmptyPatchError("Approved implementation produced no file operations")
    if not unified.endswith("\n"):
        unified += "\n"
    return unified, operations
