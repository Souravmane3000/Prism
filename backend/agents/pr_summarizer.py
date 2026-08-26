"""
backend/agents/pr_summarizer.py — PR Summarizer agent node.

Reads the complete pipeline output and produces a professional PR body:
action-oriented title, description, what changed, why, testing notes,
known limitations, and a concrete review checklist.

Does NOT create the GitHub PR itself — that is handled by the create-pr
API endpoint using the pr_draft this node produces.
"""

import json
import logging
import re
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from backend.llm import get_llm
from backend.state import (
    DebugReport,
    ImplementationPlanItem,
    PRDraft,
    PrismState,
    Subtask,
    TestResults,
)
from backend.supabase_client import save_agent_output, update_run_status

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior software engineer writing a professional GitHub pull request.

You will receive the complete output of a multi-agent pipeline that analysed a GitHub issue.
Your job is to write a PR that reads like it was authored by a thoughtful senior engineer — not
an AI summary. Focus on WHAT changed and WHY, not HOW it was implemented.

RULES:
- Title must be action-oriented (imperative verb, e.g. "Add", "Fix", "Refactor")
- Include "Closes #N" at the end of the body if the issue number is available
- Review checklist items must be SPECIFIC and ACTIONABLE — not generic
  (e.g. "Verify that the new rate-limit handler returns 429 for burst traffic" NOT "Check code quality")
- Do NOT include code blocks
- Write in clear, confident engineering prose

Return ONLY valid JSON matching this schema — no prose, no markdown fences:

{
  "title": "Action-oriented PR title",
  "body": "Full PR description in markdown (includes what/why/testing/limitations/checklist inline)",
  "what_changed": "2–4 sentence summary of what was changed",
  "why": "1–3 sentence rationale tied to the issue",
  "testing_notes": "How reviewers can verify the changes work correctly",
  "limitations": "Known limitations, edge cases not handled, or follow-up work needed",
  "review_checklist": [
    "Specific actionable checklist item 1",
    "Specific actionable checklist item 2"
  ]
}
"""


def _extract_issue_number(issue_url: Optional[str]) -> Optional[int]:
    """Extract the numeric issue ID from a GitHub issue URL."""
    if not issue_url:
        return None
    match = re.search(r"/issues/(\d+)", issue_url)
    return int(match.group(1)) if match else None


def _format_subtasks(subtasks: list[Subtask]) -> str:
    lines = []
    for st in subtasks:
        lines.append(f"- [{st['complexity'].upper()}] {st['title']}: {st['description']}")
    return "\n".join(lines)


def _format_plan(plan: list[ImplementationPlanItem]) -> str:
    lines = []
    for item in plan:
        lines.append(f"Subtask {item['subtask_id']}:")
        for step in item.get("steps", []):
            loc = f"{step['file']}"
            if step.get("function_or_symbol"):
                loc += f" → {step['function_or_symbol']}"
            lines.append(f"  {step['order']}. {step['change_description']} ({loc})")
    return "\n".join(lines)


def _format_test_results(results: Optional[TestResults], all_passed: bool) -> str:
    if results is None:
        return "Test results unavailable."
    status = "All tests passed" if all_passed else f"{results['failed_count']} test(s) failed"
    return (
        f"Framework: {results['framework']} | "
        f"Passed: {results['passed_count']} | "
        f"Failed: {results['failed_count']} | "
        f"Status: {status}"
    )


def _format_debug(report: Optional[DebugReport]) -> str:
    if report is None:
        return "No debug analysis (all tests passed or debugger was skipped)."
    lines = [report.get("summary", "")]
    for fix in report.get("fixes", []):
        lines.append(
            f"- {fix['failing_test']}: {fix['root_cause']} "
            f"(confidence: {fix['confidence']:.0%})"
        )
    return "\n".join(lines)


def _build_prompt(state: PrismState, issue_number: Optional[int]) -> str:
    subtasks_text = _format_subtasks(state.get("subtasks", []))
    plan_text = _format_plan(state.get("implementation_plan", []))
    test_text = _format_test_results(state.get("test_results"), state.get("all_tests_passed", False))
    debug_text = _format_debug(state.get("debug_report"))

    prompt = f"""## Original Issue

{state.get('issue_text', 'Issue text not available')}

## Subtask Breakdown

{subtasks_text or 'No subtasks available'}

## Engineering Implementation Plan

{plan_text or 'No plan available'}

## Test Results

{test_text}

## Debug Analysis

{debug_text}
"""
    if issue_number:
        prompt += f"\nIssue number to close: #{issue_number}\n"

    prompt += "\nWrite the professional PR JSON."
    return prompt


def _parse_pr_draft(raw: str) -> PRDraft:
    """Parse LLM JSON response into a PRDraft TypedDict."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    data = json.loads(text)
    return PRDraft(
        title=str(data.get("title", "Prism Analysis PR")),
        body=str(data.get("body", "")),
        what_changed=str(data.get("what_changed", "")),
        why=str(data.get("why", "")),
        testing_notes=str(data.get("testing_notes", "")),
        limitations=str(data.get("limitations", "")),
        review_checklist=[str(item) for item in data.get("review_checklist", [])],
    )


async def pr_summarizer_node(state: PrismState) -> dict[str, Any]:
    """
    LangGraph node: pr_summarizer.

    Reads: issue_text, issue_url, subtasks, implementation_plan,
           test_results, debug_report, all_tests_passed, run_id
    Writes: pr_draft, current_agent, messages, error
    """
    run_id: str = state["run_id"]
    logger.info("[pr_summarizer] Starting — run_id=%s", run_id)

    try:
        await update_run_status(run_id, "running", "pr_summarizer")
        await save_agent_output(run_id, "pr_summarizer", {}, "start")

        issue_number = _extract_issue_number(state.get("issue_url"))
        user_prompt = _build_prompt(state, issue_number)

        llm = get_llm(temperature=0.2)  # slightly higher for prose quality
        messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

        logger.info("[pr_summarizer] Invoking LLM for PR body generation")
        response = await llm.ainvoke(messages)
        raw_content = str(response.content)

        pr_draft = _parse_pr_draft(raw_content)
        logger.info("[pr_summarizer] PR draft generated: '%s'", pr_draft["title"])

        # Persist the full draft — GET /output reconstructs UI state from this row.
        output_payload: dict[str, Any] = {"pr_draft": dict(pr_draft)}
        await save_agent_output(run_id, "pr_summarizer", output_payload, "complete")
        await update_run_status(run_id, "completed", "pr_summarizer")

        log_line = f"[pr_summarizer] PR draft ready: '{pr_draft['title']}'"
        logger.info(log_line)

        return {
            "pr_draft": pr_draft,
            "current_agent": "pr_summarizer",
            "messages": [log_line],
        }

    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        msg = f"[pr_summarizer] Failed to generate PR: {exc}"
        logger.error(msg, exc_info=True)
        try:
            await save_agent_output(run_id, "pr_summarizer", {"error": str(exc)}, "complete")
            await update_run_status(run_id, "failed", "pr_summarizer", error=str(exc))
        except Exception as persist_exc:
            logger.error("[pr_summarizer] Failed to persist parse error: %s", persist_exc)
        return {
            "error": str(exc),
            "current_agent": "pr_summarizer",
            "messages": [msg],
        }
    except Exception as exc:
        msg = f"[pr_summarizer] Unexpected error: {exc}"
        logger.error(msg, exc_info=True)
        try:
            await save_agent_output(run_id, "pr_summarizer", {"error": str(exc)}, "complete")
            await update_run_status(run_id, "failed", "pr_summarizer", error=str(exc))
        except Exception as persist_exc:
            logger.error("[pr_summarizer] Failed to persist unexpected error: %s", persist_exc)
        return {
            "error": str(exc),
            "current_agent": "pr_summarizer",
            "messages": [msg],
        }
