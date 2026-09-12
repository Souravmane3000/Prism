"""LangGraph rollback service node. Primitive only — no Phase 4 policy."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from backend.events import (
    ROLLBACK_COMPLETED,
    ROLLBACK_FAILED,
    ROLLBACK_STARTED,
    emit_error_event,
    emit_event,
)
from backend.patch.rollback import RollbackError, rollback_to_baseline
from backend.sandbox import open_mutation_workspace
from backend.state import PrismState
from backend.supabase_client import update_run_status
from backend.workspace import LocalGitWorkspace


async def rollback_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    run_id = state["run_id"]
    github_token: str = config.get("configurable", {}).get("github_token", "")
    workspace: LocalGitWorkspace | None = None
    baseline_sha = state.get("baseline_sha")
    try:
        await update_run_status(run_id, "running", "rollback")
        await emit_event(
            run_id,
            ROLLBACK_STARTED,
            {"summary": "Rolling back to baseline", "baseline_sha": baseline_sha},
        )
        if not baseline_sha:
            raise RollbackError("rollback requires baseline_sha", reason="missing_baseline")
        workspace = open_mutation_workspace(state["repo_url"], github_token, baseline_sha)
        result = rollback_to_baseline(workspace, baseline_sha=baseline_sha)
        await emit_event(
            run_id,
            ROLLBACK_COMPLETED,
            {
                "summary": "Rollback completed",
                "patch_status": "ROLLED_BACK",
                "head_sha": result.head_sha,
                "working_tree_clean": True,
                "baseline_sha": baseline_sha,
            },
        )
        return {
            "patch_status": "ROLLED_BACK",
            "final_diff": None,
            "session_status": "RUNNING",
            "error": None,
            "current_agent": "rollback",
            "messages": ["[rollback] Repository restored to baseline"],
        }
    except Exception as exc:
        await emit_error_event(run_id, "rollback", exc)
        await emit_event(
            run_id,
            ROLLBACK_FAILED,
            {"summary": "Rollback failed", "reason": getattr(exc, "reason", "rollback_failed")},
        )
        await update_run_status(run_id, "failed", "rollback", error=str(exc))
        return {
            "session_status": "PATCH_ERROR",
            "error": str(exc),
            "current_agent": "rollback",
            "messages": [f"[rollback] {exc}"],
        }
    finally:
        if workspace is not None:
            workspace.close()
