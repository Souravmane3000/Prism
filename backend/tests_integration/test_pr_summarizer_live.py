"""
backend/tests_integration/test_pr_summarizer_live.py — Live integration tests for PR Summarizer agent.

Tests:
1. LLM PR Draft - Verify LLM can generate a PR draft
2. Full PR Summarizer - Run pr_summarizer_node with full state

Run standalone:
    python -m backend.tests_integration.test_pr_summarizer_live
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.tests_integration.harness import (
    TestHarness,
    validate_env,
    print_env_summary,
    get_test_state_minimal,
)


async def run_pr_summarizer_tests(harness: TestHarness) -> bool:
    """Run all PR summarizer agent tests."""
    
    env_vars = validate_env()
    harness.start_agent("pr_summarizer")
    
    # ── Test 1: LLM PR Draft ──────────────────────────────────────────────────
    @harness.test("LLM PR Draft Generation")
    async def test_llm_pr_draft():
        """Verify LLM can generate a structured PR draft."""
        from backend.llm import get_llm
        from langchain_core.messages import SystemMessage, HumanMessage
        
        llm = get_llm(temperature=0.2)
        
        system_prompt = """You are a senior software engineer writing a pull request description.
Given the pipeline output, produce a JSON PR draft with this schema:
{
  "title": "Short PR title (50 chars max)",
  "body": "Brief summary paragraph",
  "what_changed": "Bullet points of changes",
  "why": "Motivation for the changes",
  "testing_notes": "How to test this PR",
  "limitations": "Known limitations or edge cases",
  "review_checklist": ["Item 1", "Item 2"]
}
Return ONLY valid JSON."""

        user_prompt = """Issue: Add rate limiting to the public API

Subtasks completed:
1. Add SlowAPI middleware
2. Configure rate limits per endpoint
3. Add 429 response documentation

Test results: 5 passed, 0 failed

Generate a PR draft as JSON."""

        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        
        content = str(response.content).strip()
        
        import json
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        
        result = json.loads(content)
        
        if "title" not in result:
            raise Exception(f"Missing title in PR draft: {content[:200]}")
        
        return {
            "title_preview": result.get("title", "")[:50],
            "has_body": bool(result.get("body")),
            "checklist_items": len(result.get("review_checklist", [])),
        }
    
    await test_llm_pr_draft()
    
    # ── Test 2: Full PR Summarizer Node ───────────────────────────────────────
    @harness.test("Full PR Summarizer Node")
    async def test_full_pr_summarizer():
        """Run the complete pr_summarizer_node with full pipeline state."""
        from backend.agents.pr_summarizer import pr_summarizer_node
        from unittest.mock import AsyncMock, patch
        
        state = get_test_state_minimal()
        state["issue_text"] = "Add rate limiting to the public API to prevent abuse"
        state["subtasks"] = [
            {
                "id": "st-1",
                "title": "Add SlowAPI middleware",
                "description": "Install and configure SlowAPI for rate limiting",
                "dependencies": [],
                "likely_files": ["backend/main.py"],
                "complexity": "medium",
            },
            {
                "id": "st-2",
                "title": "Add 429 response documentation",
                "description": "Document the rate limit error response",
                "dependencies": ["st-1"],
                "likely_files": ["docs/api.md"],
                "complexity": "low",
            },
        ]
        state["implementation_plan"] = [
            {
                "subtask_id": "st-1",
                "steps": [
                    {
                        "order": 1,
                        "file": "backend/main.py",
                        "function_or_symbol": "create_app",
                        "change_description": "Add SlowAPI rate limiter middleware",
                        "rationale": "SlowAPI integrates cleanly with FastAPI",
                        "tradeoffs": ["Requires Redis in production"],
                    }
                ]
            }
        ]
        state["test_results"] = {
            "framework": "pytest",
            "passed": ["test_health", "test_rate_limiter", "test_rate_limit_exceeded"],
            "failed": [],
            "passed_count": 3,
            "failed_count": 0,
            "exit_code": 0,
            "stdout": "3 passed in 0.42s",
            "stderr": "",
        }
        state["all_tests_passed"] = True
        state["debug_report"] = None  # No failures, so no debug report
        
        # Mock Supabase calls
        with (
            patch("backend.agents.pr_summarizer.update_run_status", new=AsyncMock()),
            patch("backend.agents.pr_summarizer.save_agent_output", new=AsyncMock()),
        ):
            result = await pr_summarizer_node(state)
        
        if result.get("error"):
            raise Exception(f"PR Summarizer returned error: {result['error']}")
        
        pr_draft = result.get("pr_draft", {})
        if not pr_draft:
            raise Exception("PR Summarizer produced no draft")
        
        return {
            "title": pr_draft.get("title", "")[:50],
            "has_body": bool(pr_draft.get("body")),
            "has_checklist": bool(pr_draft.get("review_checklist")),
        }
    
    await test_full_pr_summarizer()
    
    harness.finish_agent()
    return harness.agent_results[-1].all_passed


async def main():
    """Run PR summarizer tests standalone."""
    print("=" * 60)
    print("PRISM BACKEND AGENT TEST HARNESS")
    print("Testing: PR Summarizer Agent")
    print("=" * 60)
    
    env_vars = validate_env()
    print_env_summary(env_vars)
    
    harness = TestHarness(verbose=True)
    
    success = await run_pr_summarizer_tests(harness)
    
    harness.print_summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
