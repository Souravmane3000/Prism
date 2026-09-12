"""Debugger agent — diagnose verification failures. Does not apply patches."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from backend.context_builder import assemble_code_modifier_context
from backend.events import (
    DEBUGGER_COMPLETED,
    DEBUGGER_FAILED,
    DEBUGGER_STARTED,
    emit_error_event,
    emit_event,
    sanitize_log_text,
)
from backend.llm import (
    ContextOverflowError,
    DeterministicLLMError,
    TransientLLMError,
    invoke_structured,
)
from backend.patch.apply import materialize_patch
from backend.sandbox import open_mutation_workspace
from backend.state import (
    DebugFix,
    DebugReport,
    FileMapEntry,
    ImplementationPlanItem,
    PrismState,
    TestFailure,
    TestResults,
)
from backend.supabase_client import update_run_status
from backend.verification import MIN_DEBUG_CONFIDENCE
from backend.workspace import LocalGitWorkspace

logger = logging.getLogger(__name__)

_MAX_FAILURES_TO_ANALYSE = 5
_MAX_FALLBACK_FILES = 3

_SYSTEM_PROMPT = """You diagnose a single test failure against the CURRENT applied repository.
Return ONLY JSON:
{"failing_test":"...","root_cause":"...","proposed_fix":"...","confidence":0.85,"target_files":["path"]}
Rules:
- Inspect the provided source; do not guess from failure text alone.
- Propose the smallest justified change. Natural language, not a full rewrite.
- If evidence is insufficient, set confidence below 0.7.
"""


class DebugFixModel(BaseModel):
    failing_test: str
    root_cause: str
    proposed_fix: str
    confidence: float = 0.0
    target_files: list[str] = Field(default_factory=list)


def _find_relevant_files(
    traceback_text: str,
    file_map: dict[str, list[FileMapEntry]],
    file_contents: dict[str, str],
) -> dict[str, str]:
    mentioned: set[str] = set()
    for line in traceback_text.splitlines():
        if 'File "' in line:
            start = line.index('File "') + 6
            end = line.index('"', start)
            candidate = line[start:end]
            for known_path in file_contents:
                if known_path in candidate or candidate.endswith(known_path):
                    mentioned.add(known_path)
    if not mentioned:
        ranked: list[tuple[float, str]] = []
        for entries in file_map.values():
            for entry in entries:
                ranked.append((float(entry.get("relevance_score") or 0.0), entry["path"]))
        ranked.sort(key=lambda item: item[0], reverse=True)
        for _, path in ranked[:_MAX_FALLBACK_FILES]:
            mentioned.add(path)
    return {p: file_contents[p] for p in mentioned if p in file_contents}


def _select_failures_to_analyse(
    failed_tests: list[TestFailure],
    limit: int = _MAX_FAILURES_TO_ANALYSE,
) -> list[TestFailure]:
    selected: list[TestFailure] = []
    seen: set[str] = set()
    for failure in failed_tests:
        name = str(failure.get("name") or "unknown")
        hint = str(failure.get("message") or failure.get("traceback") or "")[:80]
        fingerprint = f"{name}|{hint}"
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        selected.append(failure)
        if len(selected) >= limit:
            break
    return selected


def _find_related_plan(
    test_name: str,
    implementation_plan: list[ImplementationPlanItem],
    file_map: dict[str, list[FileMapEntry]],
) -> str:
    for plan_item in implementation_plan:
        subtask_id = plan_item["subtask_id"]
        if subtask_id in file_map:
            steps_text = "\n".join(
                f"Step {s['order']}: {s['change_description']} ({s['file']})"
                for s in plan_item.get("steps", [])
            )
            return f"Subtask {subtask_id} plan:\n{steps_text}"
    return "No related plan available"


def _load_applied_contents(state: PrismState, token: str) -> dict[str, str]:
    contents = dict(state.get("file_contents") or {})
    patch = state.get("current_patch")
    baseline = state.get("baseline_sha")
    if not patch or not baseline or not token:
        return contents
    workspace: LocalGitWorkspace | None = None
    try:
        workspace = open_mutation_workspace(state["repo_url"], token, baseline)
        materialize_patch(workspace, patch)
        for path in list(contents.keys()):
            current = workspace.read_file(path)
            if current is not None:
                contents[path] = current
        for item in patch.get("modified_files") or []:
            path = item["path"]
            current = workspace.read_file(path)
            if current is not None:
                contents[path] = current
    except Exception:
        logger.warning("[debugger] could not inspect applied workspace", exc_info=True)
    finally:
        if workspace is not None:
            workspace.close()
    return contents


async def debugger_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    run_id: str = state["run_id"]
    logger.info("[debugger] Starting — run_id=%s", run_id)
    token = config.get("configurable", {}).get("github_token", "")
    try:
        await update_run_status(run_id, "running", "debugger")
        await emit_event(run_id, DEBUGGER_STARTED, {"summary": "Debugger diagnosing failure"})

        test_results: TestResults | None = state.get("test_results")
        if test_results is None:
            raise ValueError("No test_results in state — debugger cannot run")

        failed_tests: list[TestFailure] = list(test_results.get("failed") or [])
        file_map: dict[str, list[FileMapEntry]] = state.get("file_map") or {}
        file_contents = _load_applied_contents(state, token)
        implementation_plan: list[ImplementationPlanItem] = state.get("implementation_plan") or []

        if not failed_tests:
            summary = "No failed tests to analyse; cannot generate a safe fix."
            report = DebugReport(fixes=[], summary=summary, can_generate_fix=False)
            await emit_event(
                run_id,
                DEBUGGER_FAILED,
                {"summary": summary, "reason": "debugger_no_safe_fix"},
            )
            await update_run_status(run_id, "failed", "debugger", error="debugger_no_safe_fix")
            return {
                "debug_report": report,
                "session_status": "PATCH_ERROR",
                "error": "debugger_no_safe_fix",
                "current_agent": "debugger",
                "messages": ["[debugger] No safe fix"],
            }

        fixes: list[DebugFix] = []
        to_analyse = _select_failures_to_analyse(failed_tests)
        for failure in to_analyse:
            relevant = _find_relevant_files(failure.get("traceback", ""), file_map, file_contents)
            plan_text = _find_related_plan(failure["name"], implementation_plan, file_map)
            header = (
                f"Failing test: {failure['name']}\n"
                f"Traceback:\n{sanitize_log_text(failure.get('traceback') or failure.get('message') or '', limit=3000)}\n"
                f"Plan:\n{plan_text}\n"
                f"Current diff (truncated):\n{sanitize_log_text(state.get('final_diff') or '', limit=3000)}\n"
            )
            assembled = assemble_code_modifier_context(header=header, files=relevant)
            model = await invoke_structured(
                [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=assembled.text)],
                DebugFixModel,
            )
            fixes.append(
                DebugFix(
                    failing_test=model.failing_test or failure["name"],
                    root_cause=model.root_cause,
                    proposed_fix=model.proposed_fix,
                    confidence=float(model.confidence),
                    target_files=list(model.target_files),
                )
            )

        high = [f for f in fixes if f["confidence"] >= MIN_DEBUG_CONFIDENCE and f["target_files"] and f["proposed_fix"]]
        can_fix = bool(high)
        summary = (
            f"Analysed {len(fixes)} of {len(failed_tests)} failing test(s). "
            f"{len(high)} fix proposal(s) with confidence ≥ {MIN_DEBUG_CONFIDENCE}."
        )
        report = DebugReport(fixes=fixes, summary=summary, can_generate_fix=can_fix)
        if not can_fix:
            await emit_event(
                run_id,
                DEBUGGER_FAILED,
                {"summary": summary, "reason": "debugger_no_safe_fix", "fix_count": len(fixes)},
            )
            await update_run_status(run_id, "failed", "debugger", error="debugger_no_safe_fix")
            return {
                "debug_report": report,
                "session_status": "PATCH_ERROR",
                "error": "debugger_no_safe_fix",
                "current_agent": "debugger",
                "messages": [f"[debugger] {summary}"],
            }
        await emit_event(
            run_id,
            DEBUGGER_COMPLETED,
            {"summary": summary, "fix_count": len(high)},
        )
        return {
            "debug_report": report,
            "session_status": "RUNNING",
            "error": None,
            "current_agent": "debugger",
            "messages": [f"[debugger] {summary}"],
        }
    except ContextOverflowError as exc:
        await emit_error_event(run_id, "debugger", exc)
        await emit_event(run_id, DEBUGGER_FAILED, {"summary": str(exc), "reason": "context_overflow"})
        await update_run_status(run_id, "failed", "debugger", error=str(exc))
        return {
            "debug_report": DebugReport(fixes=[], summary=str(exc), can_generate_fix=False),
            "session_status": "CONTEXT_OVERFLOW",
            "error": str(exc),
            "current_agent": "debugger",
            "messages": [f"[debugger] {exc}"],
        }
    except (TransientLLMError, DeterministicLLMError) as exc:
        await emit_error_event(run_id, "debugger", exc)
        await emit_event(run_id, DEBUGGER_FAILED, {"summary": str(exc), "reason": "llm_error"})
        await update_run_status(run_id, "failed", "debugger", error=str(exc))
        return {
            "debug_report": DebugReport(fixes=[], summary=str(exc), can_generate_fix=False),
            "session_status": "LLM_ERROR",
            "error": str(exc),
            "current_agent": "debugger",
            "messages": [f"[debugger] {exc}"],
        }
    except Exception as exc:
        await emit_error_event(run_id, "debugger", exc)
        await emit_event(run_id, DEBUGGER_FAILED, {"summary": str(exc)[:500], "reason": "debugger_no_safe_fix"})
        await update_run_status(run_id, "failed", "debugger", error=str(exc))
        return {
            "debug_report": DebugReport(fixes=[], summary=str(exc), can_generate_fix=False),
            "session_status": "PATCH_ERROR",
            "error": str(exc),
            "current_agent": "debugger",
            "messages": [f"[debugger] {exc}"],
        }
