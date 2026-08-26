"""
backend/agents/implementation_planner.py — Implementation Planner agent node.

Writes a step-by-step engineering plan for each subtask. Describes WHAT to
change, WHERE (file + function/symbol), WHY, and what the tradeoffs are.
Does NOT write code — the system prompt explicitly forbids it.
"""

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.llm import get_llm
from backend.state import (
    FileMapEntry,
    ImplementationPlanItem,
    ImplementationStep,
    PrismState,
    Subtask,
)
from backend.supabase_client import save_agent_output, update_run_status

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior software engineer writing a detailed implementation plan.

You will be given a subtask description and the relevant source files. Your job is to produce a
step-by-step engineering plan that explains exactly WHAT to change, WHERE to change it, and WHY.

CRITICAL RULES:
1. Do NOT write any code. No code blocks, no pseudocode, no snippets.
2. Reference specific file paths, function names, and class names from the provided files.
3. Each step must describe an action at a concrete location in the codebase.
4. Include tradeoffs and risks where relevant.

Return ONLY valid JSON matching this schema — no prose, no markdown fences:

{
  "subtask_id": "st-1",
  "steps": [
    {
      "order": 1,
      "file": "path/to/file.py",
      "function_or_symbol": "ClassName.method_name",
      "change_description": "What needs to change at this location and why",
      "rationale": "Why this specific approach was chosen",
      "tradeoffs": ["Tradeoff 1", "Tradeoff 2"]
    }
  ]
}
"""


def _build_subtask_prompt(
    subtask: Subtask,
    file_entries: list[FileMapEntry],
    file_contents: dict[str, str],
) -> str:
    prompt = f"""## Subtask

ID: {subtask['id']}
Title: {subtask['title']}
Description: {subtask['description']}
Complexity: {subtask['complexity']}
"""
    if subtask["dependencies"]:
        prompt += f"Depends on: {', '.join(subtask['dependencies'])}\n"

    prompt += "\n## Relevant Files\n"
    for entry in file_entries:
        path = entry["path"]
        content = file_contents.get(path, "")
        if content:
            # Truncate large files to stay within context limits
            truncated = content[:4000] if len(content) > 4000 else content
            prompt += f"\n### {path}\n\n```\n{truncated}\n```\n"
        else:
            prompt += f"\n### {path}\n(content not available)\n"

    prompt += "\nWrite the engineering plan JSON for this subtask only."
    return prompt


def _parse_plan_item(raw: str, subtask_id: str) -> ImplementationPlanItem:
    """Parse LLM JSON response into an ImplementationPlanItem."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    data = json.loads(text)
    steps: list[ImplementationStep] = []
    for s in data.get("steps", []):
        steps.append(
            ImplementationStep(
                order=int(s.get("order", 0)),
                file=str(s.get("file", "")),
                function_or_symbol=s.get("function_or_symbol"),
                change_description=str(s.get("change_description", "")),
                rationale=str(s.get("rationale", "")),
                tradeoffs=[str(t) for t in s.get("tradeoffs", [])],
            )
        )
    return ImplementationPlanItem(subtask_id=subtask_id, steps=steps)


async def implementation_planner_node(state: PrismState) -> dict[str, Any]:
    """
    LangGraph node: impl_planner.

    Reads: subtasks, file_map, file_contents, issue_text, run_id
    Writes: implementation_plan, current_agent, messages, error
    """
    run_id: str = state["run_id"]
    logger.info("[impl_planner] Starting — run_id=%s", run_id)

    try:
        await update_run_status(run_id, "running", "impl_planner")
        await save_agent_output(run_id, "impl_planner", {}, "start")

        subtasks: list[Subtask] = state.get("subtasks", [])
        file_map: dict[str, list[FileMapEntry]] = state.get("file_map", {})
        file_contents: dict[str, str] = state.get("file_contents", {})

        if not subtasks:
            raise ValueError("No subtasks available — cannot produce implementation plan")

        llm = get_llm(temperature=0.1)
        implementation_plan: list[ImplementationPlanItem] = []

        for subtask in subtasks:
            subtask_id = subtask["id"]
            file_entries = file_map.get(subtask_id, [])

            logger.info(
                "[impl_planner] Planning subtask %s with %d files", subtask_id, len(file_entries)
            )

            user_prompt = _build_subtask_prompt(subtask, file_entries, file_contents)
            messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

            try:
                response = await llm.ainvoke(messages)
                raw_content = str(response.content)
                plan_item = _parse_plan_item(raw_content, subtask_id)
                implementation_plan.append(plan_item)
                logger.info(
                    "[impl_planner] Subtask %s → %d steps", subtask_id, len(plan_item["steps"])
                )
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.error(
                    "[impl_planner] Failed to parse plan for subtask %s: %s", subtask_id, exc
                )
                # Produce a minimal valid plan item rather than failing the whole node
                implementation_plan.append(
                    ImplementationPlanItem(
                        subtask_id=subtask_id,
                        steps=[
                            ImplementationStep(
                                order=1,
                                file="unknown",
                                function_or_symbol=None,
                                change_description=f"Plan generation failed: {exc}",
                                rationale="",
                                tradeoffs=[],
                            )
                        ],
                    )
                )

        output_payload: dict[str, Any] = {
            "implementation_plan": implementation_plan,
            "subtask_count": len(implementation_plan),
        }
        await save_agent_output(run_id, "impl_planner", output_payload, "complete")

        log_line = f"[impl_planner] Produced plans for {len(implementation_plan)} subtasks"
        logger.info(log_line)

        return {
            "implementation_plan": implementation_plan,
            "current_agent": "impl_planner",
            "messages": [log_line],
        }

    except ValueError as exc:
        msg = f"[impl_planner] Failed: {exc}"
        logger.error(msg, exc_info=True)
        await save_agent_output(run_id, "impl_planner", {"error": str(exc)}, "complete")
        await update_run_status(run_id, "failed", "impl_planner", error=str(exc))
        return {
            "error": str(exc),
            "current_agent": "impl_planner",
            "messages": [msg],
        }
    except Exception as exc:
        msg = f"[impl_planner] Unexpected error: {exc}"
        logger.error(msg, exc_info=True)
        await save_agent_output(run_id, "impl_planner", {"error": str(exc)}, "complete")
        await update_run_status(run_id, "failed", "impl_planner", error=str(exc))
        return {
            "error": str(exc),
            "current_agent": "impl_planner",
            "messages": [msg],
        }
