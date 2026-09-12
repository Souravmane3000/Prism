"""Fix Patch recovery stage — Debugger diagnosis → cumulative StructuredPatch."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from backend.context_builder import assemble_code_modifier_context
from backend.events import (
    PATCH_GENERATION_COMPLETED,
    PATCH_GENERATION_FAILED,
    PATCH_GENERATION_STARTED,
    emit_error_event,
    emit_event,
)
from backend.llm import (
    ContextOverflowError,
    DeterministicLLMError,
    TransientLLMError,
    invoke_structured,
)
from backend.patch.apply import materialize_patch
from backend.patch.generate import generate_cumulative_structured_patch
from backend.patch.spec import ModificationSpec
from backend.sandbox import open_mutation_workspace
from backend.state import PrismState
from backend.supabase_client import update_run_status
from backend.workspace import LocalGitWorkspace

_SYSTEM = """You convert a Debugger diagnosis into a MINIMAL ModificationSpec.
Return ONLY JSON. Modify only the affected files. Do not rewrite the repository.
Do not delete tests to manufacture a pass. Use replacements of existing text.
{"files":[{"path":"...","operation":"modify","old_path":null,"replacements":[{"old":"...","new":"..."}],"new_content":null}],"summary":"..."}
"""


async def fix_patch_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    run_id = state["run_id"]
    workspace: LocalGitWorkspace | None = None
    try:
        await update_run_status(run_id, "running", "fix_patch")
        await emit_event(
            run_id,
            PATCH_GENERATION_STARTED,
            {"summary": "Generating fix patch", "patch_origin": "debugger"},
        )
        report = state.get("debug_report") or {}
        if not report.get("can_generate_fix"):
            raise ValueError("Debugger did not produce a safe fix")
        baseline = state.get("baseline_sha")
        patch = state.get("current_patch")
        if not baseline or not patch:
            raise ValueError("Fix Patch requires baseline_sha and current_patch")
        token = config.get("configurable", {}).get("github_token", "")
        workspace = open_mutation_workspace(state["repo_url"], token, baseline)
        materialize_patch(workspace, patch)
        files: dict[str, str] = {}
        for fix in report.get("fixes") or []:
            for path in fix.get("target_files") or []:
                content = workspace.read_file(path)
                if content is not None:
                    files[path] = content
        header = (
            f"Diagnosis:\n{report.get('summary')}\n"
            f"Fixes:\n{report.get('fixes')!r}\n"
            f"Current diff vs baseline (truncated):\n{(state.get('final_diff') or '')[:4000]}\n"
        )
        assembled = assemble_code_modifier_context(header=header, files=files)
        spec = await invoke_structured(
            [SystemMessage(content=_SYSTEM), HumanMessage(content=assembled.text)],
            ModificationSpec,
        )
        new_patch = generate_cumulative_structured_patch(spec, workspace, baseline_sha=baseline)
        await emit_event(
            run_id,
            PATCH_GENERATION_COMPLETED,
            {
                "summary": "Fix patch generated",
                "patch_origin": "debugger",
                "patch_id": new_patch["patch_id"],
                "file_count": len(new_patch["modified_files"]),
            },
        )
        return {
            "current_patch": new_patch,
            "patch_origin": "debugger",
            "patch_status": "GENERATED",
            "session_status": "RUNNING",
            "error": None,
            "current_agent": "fix_patch",
            "messages": ["[fix_patch] Fix patch GENERATED"],
        }
    except ContextOverflowError as exc:
        await emit_error_event(run_id, "fix_patch", exc)
        await emit_event(run_id, PATCH_GENERATION_FAILED, {"summary": str(exc), "patch_origin": "debugger"})
        await update_run_status(run_id, "failed", "fix_patch", error=str(exc))
        return {
            "session_status": "CONTEXT_OVERFLOW",
            "error": str(exc),
            "current_agent": "fix_patch",
            "messages": [f"[fix_patch] {exc}"],
        }
    except (TransientLLMError, DeterministicLLMError) as exc:
        await emit_error_event(run_id, "fix_patch", exc)
        await emit_event(run_id, PATCH_GENERATION_FAILED, {"summary": str(exc), "patch_origin": "debugger"})
        await update_run_status(run_id, "failed", "fix_patch", error=str(exc))
        return {
            "session_status": "LLM_ERROR",
            "error": str(exc),
            "current_agent": "fix_patch",
            "messages": [f"[fix_patch] {exc}"],
        }
    except Exception as exc:
        await emit_error_event(run_id, "fix_patch", exc)
        await emit_event(run_id, PATCH_GENERATION_FAILED, {"summary": str(exc)[:500], "patch_origin": "debugger"})
        await update_run_status(run_id, "failed", "fix_patch", error=str(exc))
        return {
            "session_status": "PATCH_ERROR",
            "error": str(exc),
            "current_agent": "fix_patch",
            "messages": [f"[fix_patch] {exc}"],
        }
    finally:
        if workspace is not None:
            workspace.close()
