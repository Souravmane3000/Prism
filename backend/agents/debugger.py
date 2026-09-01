"""
backend/agents/debugger.py — Debugger agent node.

Only reached when all_tests_passed is False. For each failing test, the
Debugger reads the traceback, cross-references the relevant file content
and implementation plan, identifies the root cause (not just the symptom),
and proposes a minimal targeted fix with a confidence score.

No full rewrites. One targeted change per failure. Does not apply fixes.

Large suites (hundreds of failures) must not block the pipeline: Modal
run_pipeline times out at 600s, and one LLM call per failure hangs the UI.
Analyse a small unique sample, then continue to PR Summarizer.
"""

import asyncio
import json
import logging
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.llm import get_llm
from backend.state import (
    DebugFix,
    DebugReport,
    FileMapEntry,
    ImplementationPlanItem,
    PrismState,
    TestFailure,
    TestResults,
)
from backend.supabase_client import save_agent_output, update_run_status

logger = logging.getLogger(__name__)

# Hard caps so 456 sequential gpt-4o-mini calls cannot stall past Modal's
# 600s worker timeout (the UI then looks "stuck" on Debugger forever).
_MAX_FAILURES_TO_ANALYSE = 5
_MAX_FALLBACK_FILES = 3
_DEBUGGER_BUDGET_SECONDS = 90.0
_LLM_CALL_TIMEOUT_SECONDS = 25.0

_SYSTEM_PROMPT = """You are a senior software engineer performing targeted test failure analysis.

You will be given a failing test's traceback, the relevant source file contents, and the
engineering plan for the related subtask. Your job is to:

1. Identify the EXACT failing line and variable/expression
2. Trace backwards to the ROOT CAUSE (not just the surface symptom)
3. Propose a MINIMAL, TARGETED fix — describe the one change that would fix the failure
4. Assign a confidence score between 0.0 and 1.0

CRITICAL RULES:
- Do NOT propose full rewrites or architectural changes
- One targeted change per failure
- Describe the fix as a natural-language change description, NOT as code
- If you cannot determine the root cause with confidence, lower your confidence score

Return ONLY valid JSON matching this schema — no prose, no markdown fences:

{
  "failing_test": "test_name",
  "root_cause": "Precise description of what is actually wrong and why",
  "proposed_fix": "Description of the one minimal change needed to fix it",
  "confidence": 0.85,
  "target_files": ["path/to/file.py"]
}
"""


def _find_relevant_files(
    traceback_text: str,
    file_map: dict[str, list[FileMapEntry]],
    file_contents: dict[str, str],
) -> dict[str, str]:
    """
    Extract file paths mentioned in the traceback and return their contents.
    Falls back to all files in file_map if traceback parsing yields nothing.
    """
    mentioned: set[str] = set()
    for line in traceback_text.splitlines():
        # Python tracebacks: '  File "path/to/file.py", line N'
        if 'File "' in line:
            start = line.index('File "') + 6
            end = line.index('"', start)
            candidate = line[start:end]
            # Normalise to relative path
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
    """Deduplicate similar failures and keep a small sample for LLM analysis."""
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
    """Return the plan text most likely related to the failing test."""
    # Simple heuristic: return the plan for the first subtask that maps to relevant files
    for plan_item in implementation_plan:
        subtask_id = plan_item["subtask_id"]
        if subtask_id in file_map:
            steps_text = "\n".join(
                f"Step {s['order']}: {s['change_description']} ({s['file']})"
                for s in plan_item.get("steps", [])
            )
            return f"Subtask {subtask_id} plan:\n{steps_text}"
    return "No related plan available"


def _build_debug_prompt(
    failure: TestFailure,
    relevant_files: dict[str, str],
    plan_text: str,
) -> str:
    prompt = f"""## Failing Test

Name: {failure['name']}

Traceback:
{failure['traceback'] or failure['message'] or 'No traceback available'}

## Related Implementation Plan

{plan_text}

## Relevant Source Files
"""
    for path, content in relevant_files.items():
        truncated = content[:3000] if len(content) > 3000 else content
        prompt += f"\n### {path}\n\n```\n{truncated}\n```\n"

    prompt += "\nAnalyse the failure and return the debug JSON."
    return prompt


async def debugger_node(state: PrismState) -> dict[str, Any]:
    """
    LangGraph node: debugger.

    Only reached when all_tests_passed is False.

    Reads: test_results, file_map, file_contents, implementation_plan, run_id
    Writes: debug_report, current_agent, messages, error
    """
    run_id: str = state["run_id"]
    logger.info("[debugger] Starting — run_id=%s", run_id)

    try:
        await update_run_status(run_id, "running", "debugger")
        await save_agent_output(run_id, "debugger", {}, "start")

        test_results: TestResults | None = state.get("test_results")
        if test_results is None:
            raise ValueError("No test_results in state — debugger cannot run")

        failed_tests: list[TestFailure] = test_results.get("failed", [])
        file_map: dict[str, list[FileMapEntry]] = state.get("file_map", {})
        file_contents: dict[str, str] = state.get("file_contents", {})
        implementation_plan: list[ImplementationPlanItem] = state.get("implementation_plan", [])

        if not failed_tests:
            logger.warning("[debugger] No failed tests to analyse")
            exit_code = int(test_results.get("exit_code", 0) or 0)
            stderr = str(test_results.get("stderr") or "").strip()
            if exit_code != 0:
                detail = stderr[:500] if stderr else "The sandbox exited before any tests were collected."
                summary = (
                    f"The repository test suite did not execute (exit code {exit_code}). {detail}"
                )
            else:
                summary = "No failed tests to debug"
            empty_report = DebugReport(fixes=[], summary=summary)
            await save_agent_output(
                run_id, "debugger", {"debug_report": dict(empty_report)}, "complete"
            )
            return {
                "debug_report": empty_report,
                "current_agent": "debugger",
                "messages": ["[debugger] No failed tests"],
            }

        llm = get_llm(temperature=0.1)
        fixes: list[DebugFix] = []
        to_analyse = _select_failures_to_analyse(failed_tests)
        deadline = time.monotonic() + _DEBUGGER_BUDGET_SECONDS
        logger.info(
            "[debugger] Analysing %d of %d unique-sampled failure(s)",
            len(to_analyse),
            len(failed_tests),
        )

        for failure in to_analyse:
            if time.monotonic() >= deadline:
                logger.warning(
                    "[debugger] Time budget exhausted after %d fix(es) — skipping remainder",
                    len(fixes),
                )
                break
            relevant_files = _find_relevant_files(
                failure.get("traceback", ""), file_map, file_contents
            )
            plan_text = _find_related_plan(failure["name"], implementation_plan, file_map)
            user_prompt = _build_debug_prompt(failure, relevant_files, plan_text)
            messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

            try:
                response = await asyncio.wait_for(
                    llm.ainvoke(messages),
                    timeout=_LLM_CALL_TIMEOUT_SECONDS,
                )
                raw = str(response.content).strip()
                if raw.startswith("```"):
                    lines = raw.split("\n")
                    raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
                data = json.loads(raw)
                fixes.append(
                    DebugFix(
                        failing_test=str(data.get("failing_test", failure["name"])),
                        root_cause=str(data.get("root_cause", "")),
                        proposed_fix=str(data.get("proposed_fix", "")),
                        confidence=float(data.get("confidence", 0.5)),
                        target_files=[str(f) for f in data.get("target_files", [])],
                    )
                )
                logger.info(
                    "[debugger] Analysed failure '%s' — confidence=%.2f",
                    failure["name"],
                    fixes[-1]["confidence"],
                )
            except (TimeoutError, asyncio.TimeoutError):
                logger.error(
                    "[debugger] LLM timed out for '%s'", failure["name"]
                )
                fixes.append(
                    DebugFix(
                        failing_test=failure["name"],
                        root_cause="Debugger LLM call timed out",
                        proposed_fix="Manual investigation required",
                        confidence=0.0,
                        target_files=[],
                    )
                )
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.error(
                    "[debugger] Failed to parse debug output for '%s': %s", failure["name"], exc
                )
                fixes.append(
                    DebugFix(
                        failing_test=failure["name"],
                        root_cause=f"Analysis failed: {exc}",
                        proposed_fix="Manual investigation required",
                        confidence=0.0,
                        target_files=[],
                    )
                )

        omitted = max(0, len(failed_tests) - len(fixes))
        high_conf = [f for f in fixes if f["confidence"] >= 0.7]
        summary = (
            f"Analysed {len(fixes)} of {len(failed_tests)} failing test(s). "
            f"{len(high_conf)} fix proposal(s) with confidence ≥ 0.7."
        )
        if omitted:
            summary += (
                f" {omitted} additional failure(s) were not analysed "
                f"(cap {_MAX_FAILURES_TO_ANALYSE} unique samples)."
            )
        debug_report = DebugReport(fixes=fixes, summary=summary)

        output_payload: dict[str, Any] = {"debug_report": dict(debug_report)}
        await save_agent_output(run_id, "debugger", output_payload, "complete")

        log_line = f"[debugger] {summary}"
        logger.info(log_line)

        return {
            "debug_report": debug_report,
            "current_agent": "debugger",
            "messages": [log_line],
        }

    except ValueError as exc:
        msg = f"[debugger] Failed: {exc}"
        logger.error(msg, exc_info=True)
        partial_report = DebugReport(fixes=[], summary=str(exc))
        await save_agent_output(
            run_id,
            "debugger",
            {"error": str(exc), "debug_report": dict(partial_report)},
            "complete",
        )
        return {
            "debug_report": partial_report,
            "error": str(exc),
            "current_agent": "debugger",
            "messages": [msg],
        }
    except Exception as exc:
        msg = f"[debugger] Unexpected error: {exc}"
        logger.error(msg, exc_info=True)
        partial_report = DebugReport(fixes=[], summary=str(exc))
        await save_agent_output(
            run_id,
            "debugger",
            {"error": str(exc), "debug_report": dict(partial_report)},
            "complete",
        )
        return {
            "debug_report": partial_report,
            "error": str(exc),
            "current_agent": "debugger",
            "messages": [msg],
        }
