"""LangGraph code_modifier node — generates a StructuredPatch from the plan."""

from __future__ import annotations

import logging
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
from backend.patch.generate import generate_structured_patch
from backend.patch.spec import ModificationSpec
from backend.sandbox import open_mutation_workspace
from backend.state import PrismState
from backend.supabase_client import update_run_status
from backend.workspace import LocalGitWorkspace

logger = logging.getLogger(__name__)

_SYSTEM = """You generate a focused ModificationSpec JSON for an approved implementation plan.
Return ONLY JSON matching:
{"files":[{"path":"...","operation":"modify"|"add"|"delete","old_path":null,"replacements":[{"old":"...","new":"..."}],"new_content":null}],"summary":"..."}
Rules:
- Prefer replacements of existing text over rewriting whole files.
- Only change files required by the plan.
- Do not include secrets or .git paths.
"""


async def code_modifier_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    run_id = state["run_id"]
    workspace: LocalGitWorkspace | None = None
    try:
        await update_run_status(run_id, "running", "code_modifier")
        await emit_event(
            run_id,
            PATCH_GENERATION_STARTED,
            {"summary": "Generating implementation patch", "patch_origin": "code_modifier"},
        )
        baseline = state.get("baseline_sha")
        token = config.get("configurable", {}).get("github_token", "")
        if not baseline:
            raise ValueError("Code Modifier requires baseline_sha from Planner")
        workspace = open_mutation_workspace(state["repo_url"], token, baseline)
        files: dict[str, str] = {}
        for item in state.get("implementation_plan") or []:
            for step in item.get("steps") or []:
                path = step.get("file")
                if path and path not in files:
                    content = workspace.read_file(path)
                    if content is not None:
                        files[path] = content
        if not files:
            files = dict(list((state.get("file_contents") or {}).items())[:8])
        header = (
            f"Issue:\n{state.get('issue_text') or ''}\n\n"
            f"Plan:\n{state.get('implementation_plan')!r}\n"
        )
        assembled = assemble_code_modifier_context(header=header, files=files)
        spec = await invoke_structured(
            [SystemMessage(content=_SYSTEM), HumanMessage(content=assembled.text)],
            ModificationSpec,
        )
        patch = generate_structured_patch(spec, workspace, baseline_sha=baseline)
        await emit_event(
            run_id,
            PATCH_GENERATION_COMPLETED,
            {
                "summary": "Implementation patch generated",
                "patch_origin": "code_modifier",
                "patch_id": patch["patch_id"],
                "file_count": len(patch["modified_files"]),
            },
        )
        return {
            "current_patch": patch,
            "patch_origin": "code_modifier",
            "patch_status": "GENERATED",
            "session_status": "RUNNING",
            "error": None,
            "current_agent": "code_modifier",
            "messages": ["[code_modifier] Patch GENERATED"],
        }
    except ContextOverflowError as exc:
        await emit_error_event(run_id, "code_modifier", exc)
        await emit_event(run_id, PATCH_GENERATION_FAILED, {"summary": str(exc), "reason": "context_overflow"})
        await update_run_status(run_id, "failed", "code_modifier", error=str(exc))
        return {
            "session_status": "CONTEXT_OVERFLOW",
            "error": str(exc),
            "current_agent": "code_modifier",
            "messages": [f"[code_modifier] {exc}"],
        }
    except (TransientLLMError, DeterministicLLMError) as exc:
        await emit_error_event(run_id, "code_modifier", exc)
        await emit_event(run_id, PATCH_GENERATION_FAILED, {"summary": str(exc), "reason": "llm_error"})
        await update_run_status(run_id, "failed", "code_modifier", error=str(exc))
        return {
            "session_status": "LLM_ERROR",
            "error": str(exc),
            "current_agent": "code_modifier",
            "messages": [f"[code_modifier] {exc}"],
        }
    except Exception as exc:
        await emit_error_event(run_id, "code_modifier", exc)
        await emit_event(run_id, PATCH_GENERATION_FAILED, {"summary": str(exc)[:500], "reason": "patch_error"})
        await update_run_status(run_id, "failed", "code_modifier", error=str(exc))
        return {
            "session_status": "PATCH_ERROR",
            "error": str(exc),
            "current_agent": "code_modifier",
            "messages": [f"[code_modifier] {exc}"],
        }
    finally:
        if workspace is not None:
            workspace.close()
