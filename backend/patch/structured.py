"""Canonical StructuredPatch construction."""

from __future__ import annotations

import hashlib
import uuid

from backend.state import PatchFileOperation, StructuredPatch

SCHEMA_VERSION = "1"


def checksum_sha256(unified_diff: str) -> str:
    return hashlib.sha256(unified_diff.encode("utf-8")).hexdigest()


def build_structured_patch(
    *,
    baseline_sha: str,
    unified_diff: str,
    modified_files: list[PatchFileOperation],
    patch_id: str | None = None,
) -> StructuredPatch:
    diff = unified_diff if unified_diff.endswith("\n") else unified_diff + "\n"
    return StructuredPatch(
        schema_version="1",
        patch_id=patch_id or str(uuid.uuid4()),
        baseline_sha=baseline_sha,
        unified_diff=diff,
        modified_files=modified_files,
        checksum_sha256=checksum_sha256(diff),
    )
