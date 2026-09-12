"""Phase 5 policy node: HITL revision limit → FAILED_MAX_REVISIONS."""

from __future__ import annotations

from typing import Any

from backend.events import REVISION_LIMIT_REACHED, emit_event
from backend.state import MAX_HITL_REVISIONS, PrismState
from backend.supabase_client import update_run_status


async def max_revisions_failure_node(state: PrismState) -> dict[str, Any]:
    run_id = state["run_id"]
    count = int(state.get("hitl_revision_count") or 0)
    await emit_event(
        run_id,
        REVISION_LIMIT_REACHED,
        {
            "summary": f"Failed after {MAX_HITL_REVISIONS} HITL revision cycles",
            "session_status": "FAILED_MAX_REVISIONS",
            "hitl_revision_count": count,
            "max_hitl_revisions": MAX_HITL_REVISIONS,
        },
    )
    await update_run_status(
        run_id,
        "failed",
        "max_revisions_failure",
        error="FAILED_MAX_REVISIONS",
    )
    return {
        "session_status": "FAILED_MAX_REVISIONS",
        "current_agent": "max_revisions_failure",
        "messages": ["[max_revisions_failure] FAILED_MAX_REVISIONS"],
    }
