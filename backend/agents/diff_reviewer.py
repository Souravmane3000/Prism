"""LangGraph diff_reviewer node — independent review before HITL #3.

Does not apply or modify code. Produces APPROVE | REQUEST_CHANGES only.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from backend.context_builder import assemble_code_modifier_context
from backend.events import (
    REVIEW_DIFF_COMPLETED,
    REVIEW_DIFF_FAILED,
    REVIEW_DIFF_STARTED,
    emit_error_event,
    emit_event,
)
from backend.llm import (
    ContextOverflowError,
    DeterministicLLMError,
    TransientLLMError,
    invoke_structured,
)
from backend.state import PrismState, ReviewerFinding
from backend.supabase_client import update_run_status

logger = logging.getLogger(__name__)

_SYSTEM = """You are an independent Diff Reviewer for Prism.
You review a proposed implementation patch against the approved implementation plan and issue.
You do NOT write code, run tests, or apply the patch.

Assess at minimum:
- correctness relative to the implementation plan
- whether the issue appears addressed
- unintended behavioural changes / obvious regressions
- missing required changes
- suspicious or unrelated modifications
- unsafe or security-sensitive changes
- test coverage implications (reason from the diff; do not run tests)
- consistency with existing architecture
- whether modified files are justified / patch unnecessarily broad
- obvious API/interface breakage, dependency/config problems
- generated-file or unrelated-file modifications

Rules:
- Base findings only on the provided diff and context. Do not invent unsupported issues.
- Distinguish blocking concerns from minor observations via severity.
- Prefer specific evidence over vague statements.
- File-level linking is required. Only include line_start/line_end when clearly supported by the diff; otherwise null.
- Never include secrets, tokens, or credentials in findings.
- Primary decision must be exactly APPROVE or REQUEST_CHANGES (no numeric score).
- APPROVE means no blocking issue that should prevent human review — human approval is still mandatory.
- REQUEST_CHANGES means issues should be addressed before apply.

Return ONLY JSON matching the schema.
"""


class _FindingModel(BaseModel):
    severity: Literal["blocking", "major", "minor"]
    category: Literal[
        "correctness",
        "regression",
        "security",
        "scope",
        "tests",
        "architecture",
        "api",
        "other",
    ]
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    description: str
    why_it_matters: str
    recommended_action: str


class DiffReviewResult(BaseModel):
    decision: Literal["APPROVE", "REQUEST_CHANGES"]
    summary: str
    risk_level: Literal["low", "medium", "high"] = "medium"
    reviewed_files: list[str] = Field(default_factory=list)
    findings: list[_FindingModel] = Field(default_factory=list)


def _patch_files(state: PrismState) -> list[str]:
    patch = state.get("current_patch") or {}
    files: list[str] = []
    for item in patch.get("modified_files") or []:
        path = item.get("path")
        if path:
            files.append(path)
        old = item.get("old_path")
        if old:
            files.append(old)
    return files


def _build_review_header(state: PrismState) -> str:
    patch = state.get("current_patch") or {}
    diff = patch.get("unified_diff") or ""
    files = _patch_files(state)
    return (
        f"Issue:\n{state.get('issue_text') or ''}\n\n"
        f"Approved implementation plan:\n{state.get('implementation_plan')!r}\n\n"
        f"Patch metadata:\n"
        f"- patch_id: {patch.get('patch_id')}\n"
        f"- patch_status: {state.get('patch_status')}\n"
        f"- patch_origin: {state.get('patch_origin')}\n"
        f"- baseline_sha: {patch.get('baseline_sha') or state.get('baseline_sha')}\n"
        f"- hitl_revision_count: {state.get('hitl_revision_count', 0)}\n"
        f"- modified_files: {files}\n\n"
        f"Proposed unified diff:\n```diff\n{diff}\n```\n"
    )


def _to_findings(models: list[_FindingModel]) -> list[ReviewerFinding]:
    findings: list[ReviewerFinding] = []
    for item in models:
        findings.append(
            {
                "severity": item.severity,
                "category": item.category,
                "file_path": item.file_path,
                "line_start": item.line_start,
                "line_end": item.line_end,
                "description": item.description,
                "why_it_matters": item.why_it_matters,
                "recommended_action": item.recommended_action,
            }
        )
    return findings


async def diff_reviewer_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    """Review a VALIDATED code_modifier patch. Never reads github_token into prompts."""
    del config
    run_id = state["run_id"]
    try:
        await update_run_status(run_id, "running", "diff_reviewer")
        await emit_event(
            run_id,
            REVIEW_DIFF_STARTED,
            {
                "summary": "Reviewing proposed implementation diff",
                "patch_status": state.get("patch_status"),
                "patch_origin": state.get("patch_origin"),
                "hitl_revision_count": state.get("hitl_revision_count", 0),
            },
        )
        if state.get("patch_status") != "VALIDATED" or not state.get("current_patch"):
            raise ValueError("Diff Reviewer requires a VALIDATED current_patch")
        if state.get("patch_origin") == "debugger":
            raise ValueError("Diff Reviewer must not review debugger-origin patches")

        files: dict[str, str] = {}
        contents = state.get("file_contents") or {}
        for path in _patch_files(state):
            if path in contents:
                files[path] = contents[path]
        header = _build_review_header(state)
        assembled = assemble_code_modifier_context(header=header, files=files)
        result = await invoke_structured(
            [SystemMessage(content=_SYSTEM), HumanMessage(content=assembled.text)],
            DiffReviewResult,
        )
        reviewed_files = result.reviewed_files or _patch_files(state)
        findings = _to_findings(result.findings)
        await emit_event(
            run_id,
            REVIEW_DIFF_COMPLETED,
            {
                "summary": result.summary[:500],
                "decision": result.decision,
                "risk_level": result.risk_level,
                "finding_count": len(findings),
                "reviewed_files": reviewed_files[:50],
            },
        )
        return {
            "review_decision": result.decision,
            "review_summary": result.summary,
            "review_risk_level": result.risk_level,
            "reviewed_files": reviewed_files,
            "reviewer_findings": findings,
            "patch_status": "REVIEWED",
            "session_status": "AWAITING_HITL_3",
            "error": None,
            "current_agent": "diff_reviewer",
            "messages": [f"[diff_reviewer] {result.decision}"],
        }
    except ContextOverflowError as exc:
        await emit_error_event(run_id, "diff_reviewer", exc)
        await emit_event(
            run_id,
            REVIEW_DIFF_FAILED,
            {"summary": str(exc), "reason": "context_overflow"},
        )
        await update_run_status(run_id, "failed", "diff_reviewer", error=str(exc))
        return {
            "session_status": "CONTEXT_OVERFLOW",
            "error": str(exc),
            "current_agent": "diff_reviewer",
            "messages": [f"[diff_reviewer] {exc}"],
        }
    except (TransientLLMError, DeterministicLLMError) as exc:
        await emit_error_event(run_id, "diff_reviewer", exc)
        await emit_event(
            run_id,
            REVIEW_DIFF_FAILED,
            {"summary": str(exc), "reason": "llm_error"},
        )
        await update_run_status(run_id, "failed", "diff_reviewer", error=str(exc))
        return {
            "session_status": "LLM_ERROR",
            "error": str(exc),
            "current_agent": "diff_reviewer",
            "messages": [f"[diff_reviewer] {exc}"],
        }
    except Exception as exc:
        await emit_error_event(run_id, "diff_reviewer", exc)
        await emit_event(
            run_id,
            REVIEW_DIFF_FAILED,
            {"summary": str(exc)[:500], "reason": "review_error"},
        )
        await update_run_status(run_id, "failed", "diff_reviewer", error=str(exc))
        return {
            "session_status": "LLM_ERROR",
            "error": str(exc),
            "current_agent": "diff_reviewer",
            "messages": [f"[diff_reviewer] {exc}"],
        }
