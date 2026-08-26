"""
backend/agents/planner.py — Planner agent node.

The Planner is the most reasoning-intensive agent. It reads the GitHub issue
and repository structure then decomposes the work into ordered subtasks with
dependencies, file hints, and complexity estimates.

The github_token is NOT in state — it is read from
config["configurable"]["github_token"] to keep it out of the LangGraph
PostgreSQL checkpointer.
"""

import json
import logging
from typing import Any

from github import Github
from github.GithubException import GithubException
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from backend.github_client import (
    get_file_content,
    get_file_tree,
    get_github_client,
    get_issue,
    get_repo,
)
from backend.llm import get_llm
from backend.state import PrismState, Subtask
from backend.supabase_client import save_agent_output, update_run_status

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior software engineer performing issue triage and task decomposition.

Given a GitHub issue and a repository file tree, produce a JSON array of ordered subtasks that
collectively resolve the issue. Each subtask must be independently actionable.

Return ONLY valid JSON — no prose, no markdown fences, no explanation before or after.

Schema for each subtask object:
{
  "id": "st-1",                         // sequential, e.g. st-1, st-2
  "title": "Short imperative title",
  "description": "What needs to be done and why — be specific",
  "dependencies": ["st-1"],             // ids of subtasks this one depends on (empty list if none)
  "likely_files": ["src/foo.py"],       // 1–5 paths from the file tree that are most relevant
  "complexity": "low" | "medium" | "high"
}

Rules:
- Produce 2–8 subtasks. Single-file trivial changes may need only 2; large features up to 8.
- Dependencies must be a subset of subtask ids that appear earlier in the array.
- likely_files must be actual paths from the provided file tree.
- complexity must be exactly one of: low, medium, high.
- Do not include tests as a separate subtask unless the issue specifically asks to add tests.
"""

# README-like filenames to try when fetching context
_README_CANDIDATES = ["README.md", "README.rst", "README.txt", "README", "docs/README.md"]
_CONFIG_CANDIDATES = [
    "pyproject.toml", "setup.py", "setup.cfg", "package.json",
    "requirements.txt", "Makefile", ".github/workflows/ci.yml",
]


def _build_user_prompt(
    issue_body: str,
    file_tree: list[str],
    readme: str,
    config_snippets: dict[str, str],
) -> str:
    tree_text = "\n".join(file_tree[:500])  # cap at 500 paths to stay within context
    config_text = "\n\n".join(
        f"--- {path} ---\n{content[:1000]}" for path, content in config_snippets.items()
    )
    prompt = f"""## GitHub Issue

{issue_body}

## Repository File Tree

{tree_text}
"""
    if readme:
        prompt += f"\n## README\n\n{readme[:3000]}\n"
    if config_text:
        prompt += f"\n## Key Config Files\n\n{config_text}\n"
    prompt += "\nDecompose the issue into ordered subtasks. Return JSON only."
    return prompt


def _parse_subtasks(raw: str) -> list[Subtask]:
    """Extract JSON from the LLM response and validate the subtask shape."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of subtasks")
    subtasks: list[Subtask] = []
    for item in data:
        subtasks.append(
            Subtask(
                id=str(item["id"]),
                title=str(item["title"]),
                description=str(item["description"]),
                dependencies=[str(d) for d in item.get("dependencies", [])],
                likely_files=[str(f) for f in item.get("likely_files", [])],
                complexity=str(item.get("complexity", "medium")),
            )
        )
    return subtasks


async def planner_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    """
    LangGraph node: planner.

    Reads: repo_url, issue_url, issue_text, run_id
    Config: config["configurable"]["github_token"]
    Writes: repo_tree, subtasks, issue_text (if fetched), current_agent, messages, error
    """
    run_id: str = state["run_id"]
    github_token: str = config.get("configurable", {}).get("github_token", "")
    logger.info("[planner] Starting — run_id=%s", run_id)

    try:
        await update_run_status(run_id, "running", "planner")
        await save_agent_output(run_id, "planner", {}, "start")

        # ── Fetch issue body ──────────────────────────────────────────────────
        github_client: Github = get_github_client(github_token)
        repo = get_repo(github_client, state["repo_url"])

        issue_body: str = state.get("issue_text") or ""
        if state.get("issue_url") and not issue_body:
            try:
                issue_data = get_issue(repo, state["issue_url"])
                issue_body = f"**{issue_data['title']}**\n\n{issue_data['body']}"
            except GithubException as exc:
                logger.warning("[planner] Could not fetch issue URL, using issue_text: %s", exc)

        if not issue_body:
            raise ValueError("No issue content available — provide issue_url or issue_text")

        # ── Fetch repo file tree ───────────────────────────────────────────────
        file_tree: list[str] = get_file_tree(repo)
        logger.info("[planner] File tree has %d entries", len(file_tree))

        # ── Fetch README and config files for context ──────────────────────────
        readme = ""
        for candidate in _README_CANDIDATES:
            if candidate in file_tree or candidate.lower() in [p.lower() for p in file_tree]:
                content = get_file_content(repo, candidate)
                if content:
                    readme = content
                    break

        config_snippets: dict[str, str] = {}
        for candidate in _CONFIG_CANDIDATES:
            if candidate in file_tree:
                content = get_file_content(repo, candidate)
                if content:
                    config_snippets[candidate] = content

        # ── Call LLM ──────────────────────────────────────────────────────────
        llm = get_llm(temperature=0.1)
        user_prompt = _build_user_prompt(issue_body, file_tree, readme, config_snippets)
        messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

        logger.info("[planner] Invoking LLM for subtask decomposition")
        response = await llm.ainvoke(messages)
        raw_content: str = str(response.content)

        # ── Parse response ─────────────────────────────────────────────────────
        subtasks = _parse_subtasks(raw_content)
        logger.info("[planner] Produced %d subtasks", len(subtasks))

        # ── Persist to Supabase ────────────────────────────────────────────────
        output_payload: dict[str, Any] = {
            "subtasks": subtasks,
            "repo_tree_count": len(file_tree),
        }
        await save_agent_output(run_id, "planner", output_payload, "complete")

        log_line = f"[planner] Decomposed issue into {len(subtasks)} subtasks"
        logger.info(log_line)

        return {
            "repo_tree": file_tree,
            "subtasks": subtasks,
            "issue_text": issue_body,
            "current_agent": "planner",
            "messages": [log_line],
        }

    except (GithubException, ValueError, json.JSONDecodeError, OutputParserException) as exc:
        msg = f"[planner] Failed: {exc}"
        logger.error(msg, exc_info=True)
        await save_agent_output(run_id, "planner", {"error": str(exc)}, "complete")
        await update_run_status(run_id, "failed", "planner", error=str(exc))
        return {
            "error": str(exc),
            "current_agent": "planner",
            "messages": [msg],
        }
    except Exception as exc:
        msg = f"[planner] Unexpected error: {exc}"
        logger.error(msg, exc_info=True)
        await save_agent_output(run_id, "planner", {"error": str(exc)}, "complete")
        await update_run_status(run_id, "failed", "planner", error=str(exc))
        return {
            "error": str(exc),
            "current_agent": "planner",
            "messages": [msg],
        }
