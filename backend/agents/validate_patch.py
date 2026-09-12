"""LangGraph validate_patch service node."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from backend.events import PATCH_VALIDATION_COMPLETED, PATCH_VALIDATION_STARTED, emit_error_event, emit_event
from backend.patch.validate import validate_structured_patch
from backend.state import PrismState
from backend.supabase_client import update_run_status


async def validate_patch_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    run_id = state["run_id"]
    await update_run_status(run_id, "running", "validate_patch")
    await emit_event(
        run_id,
        PATCH_VALIDATION_STARTED,
        {"summary": "Validating patch", "patch_origin": state.get("patch_origin")},
    )
    try:
        result = validate_structured_patch(
            state.get("current_patch"),
            expected_baseline=state.get("baseline_sha"),
        )
        status = "VALIDATED" if result.ok else "INVALID"
        await emit_event(
            run_id,
            PATCH_VALIDATION_COMPLETED,
            {
                "summary": "Patch validated" if result.ok else "Patch invalid",
                "patch_status": status,
                "reason": result.reason,
                "patch_origin": state.get("patch_origin"),
            },
        )
        if not result.ok:
            await update_run_status(run_id, "failed", "validate_patch", error=result.reason)
        return {
            "patch_status": status,
            "session_status": "RUNNING" if result.ok else "PATCH_ERROR",
            "error": None if result.ok else result.reason,
            "current_agent": "validate_patch",
            "messages": [f"[validate_patch] {status}"],
        }
    except Exception as exc:
        await emit_error_event(run_id, "validate_patch", exc)
        await update_run_status(run_id, "failed", "validate_patch", error=str(exc))
        return {
            "patch_status": "INVALID",
            "session_status": "PATCH_ERROR",
            "error": str(exc),
            "current_agent": "validate_patch",
            "messages": [f"[validate_patch] {exc}"],
        }
