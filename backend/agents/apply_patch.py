"""LangGraph apply_patch service node. Does not enable HITL #3."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from backend.events import (
    PATCH_APPLICATION_COMPLETED,
    PATCH_APPLICATION_FAILED,
    PATCH_APPLICATION_STARTED,
    emit_error_event,
    emit_event,
)
from backend.patch.apply import ApplyError, apply_patch
from backend.sandbox import open_mutation_workspace
from backend.state import PrismState, StructuredPatch
from backend.supabase_client import update_run_status
from backend.workspace import LocalGitWorkspace


def _payload(
    *,
    summary: str,
    patch_status: str | None,
    patch: StructuredPatch | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"summary": summary, "patch_status": patch_status}
    if patch is not None:
        body["patch_id"] = patch["patch_id"]
        body["baseline_sha"] = patch["baseline_sha"]
        body["file_count"] = len(patch.get("modified_files") or [])
    body.update({k: v for k, v in extra.items() if v is not None})
    return body


async def apply_patch_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    run_id = state["run_id"]
    github_token: str = config.get("configurable", {}).get("github_token", "")
    workspace: LocalGitWorkspace | None = None
    patch: StructuredPatch | None = state.get("current_patch")
    incoming_status = state.get("patch_status")
    try:
        await update_run_status(run_id, "running", "apply_patch")
        await emit_event(
            run_id,
            PATCH_APPLICATION_STARTED,
            _payload(summary="Applying patch", patch_status=incoming_status, patch=patch),
        )
        if incoming_status == "APPLIED":
            raise ApplyError("Patch is already APPLIED", reason="duplicate_apply")
        baseline_sha = state.get("baseline_sha")
        if not patch or not baseline_sha:
            raise ApplyError("apply_patch requires current_patch and baseline_sha", reason="missing_patch")
        workspace = open_mutation_workspace(state["repo_url"], github_token, baseline_sha)
        result = apply_patch(
            patch=patch,
            patch_status=incoming_status,
            baseline_sha=baseline_sha,
            workspace=workspace,
        )
        await emit_event(
            run_id,
            PATCH_APPLICATION_COMPLETED,
            _payload(
                summary="Patch applied",
                patch_status="APPLIED",
                patch=patch,
                head_sha=result.head_sha,
                working_tree_clean=False,
            ),
        )
        return {
            "patch_status": "APPLIED",
            "modified_files": result.modified_files,
            "final_diff": result.final_diff,
            "session_status": "RUNNING",
            "error": None,
            "current_agent": "apply_patch",
            "messages": ["[apply_patch] Patch APPLIED"],
        }
    except Exception as exc:
        reason = getattr(exc, "reason", "patch_application_failed")
        await emit_error_event(run_id, "apply_patch", exc)
        await emit_event(
            run_id,
            PATCH_APPLICATION_FAILED,
            _payload(summary="Patch application failed", patch_status=incoming_status, patch=patch, reason=reason),
        )
        await update_run_status(run_id, "failed", "apply_patch", error=str(exc))
        kept = "APPLIED" if isinstance(exc, ApplyError) and exc.reason == "duplicate_apply" else incoming_status
        return {
            "patch_status": kept,
            "session_status": "PATCH_ERROR",
            "error": str(exc),
            "current_agent": "apply_patch",
            "messages": [f"[apply_patch] {exc}"],
        }
    finally:
        if workspace is not None:
            workspace.close()
