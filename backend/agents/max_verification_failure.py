"""Phase 4 policy node: successful rollback → FAILED_MAX_VERIFICATION."""

from __future__ import annotations

from typing import Any

from backend.events import emit_event
from backend.state import PrismState
from backend.supabase_client import update_run_status


async def max_verification_failure_node(state: PrismState) -> dict[str, Any]:
    run_id = state["run_id"]
    if state.get("patch_status") != "ROLLED_BACK":
        return {
            "session_status": "PATCH_ERROR",
            "current_agent": "max_verification_failure",
            "messages": ["[max_verification_failure] Rollback was not verified"],
        }
    await emit_event(
        run_id,
        "run.completed",
        {
            "summary": "Failed after 3 verification attempts",
            "session_status": "FAILED_MAX_VERIFICATION",
            "verification_attempts": state.get("verification_attempts"),
        },
    )
    await update_run_status(
        run_id,
        "failed",
        "max_verification_failure",
        error="FAILED_MAX_VERIFICATION",
    )
    return {
        "session_status": "FAILED_MAX_VERIFICATION",
        "current_agent": "max_verification_failure",
        "messages": ["[max_verification_failure] FAILED_MAX_VERIFICATION"],
    }
