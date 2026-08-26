"""
backend/tests_integration/test_planner_live.py — Live integration tests for Planner agent.

Tests:
1. LLM Connection - Verify OpenAI API is reachable
2. LLM Authentication - Verify Modal auth key works
3. GitHub Fetch - Verify repo tree fetch works
4. Full Planner - Run planner_node with real inputs

Run standalone:
    python -m backend.tests_integration.test_planner_live
"""

import asyncio
import os
import sys
from pathlib import Path

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx
from langchain_core.messages import HumanMessage

from backend.tests_integration.harness import (
    TestHarness,
    validate_env,
    print_env_summary,
    get_test_state_minimal,
    get_test_config,
    TEST_REPO_URL,
    TEST_ISSUE_TEXT,
)


async def run_planner_tests(harness: TestHarness) -> bool:
    """Run all planner agent tests."""
    
    env_vars = validate_env()
    harness.start_agent("planner")
    
    # ── Test 1: LLM Connection ────────────────────────────────────────────────
    @harness.test("LLM Connection")
    async def test_llm_connection():
        """Verify the OpenAI API is reachable."""
        endpoint = "https://api.openai.com/v1/models"
        api_key = env_vars["OPENAI_API_KEY"]

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if response.status_code == 200:
                return {"endpoint": endpoint, "status": response.status_code}
            if response.status_code == 401:
                raise Exception(
                    f"OpenAI 401 Unauthorized — check OPENAI_API_KEY: {response.text[:200]}"
                )
            raise Exception(
                f"Unexpected status {response.status_code}: {response.text[:200]}"
            )
    
    await test_llm_connection()
    
    # ── Test 2: LLM Authentication ────────────────────────────────────────────
    @harness.test("LLM Authentication")
    async def test_llm_auth():
        """Verify the Modal auth key works by making a simple LLM call."""
        from backend.llm import get_llm
        
        llm = get_llm(temperature=0.0)
        
        # Simple test prompt
        response = await llm.ainvoke([
            HumanMessage(content="Reply with exactly: TEST_OK")
        ])
        
        content = str(response.content).strip()
        if "TEST_OK" in content.upper() or len(content) > 0:
            return {"response_preview": content[:100]}
        else:
            raise Exception(f"LLM returned empty response")
    
    await test_llm_auth()
    
    # ── Test 3: GitHub Fetch ──────────────────────────────────────────────────
    @harness.test("GitHub API Fetch")
    async def test_github_fetch():
        """Verify GitHub API access works (repo tree fetch)."""
        from backend.github_client import get_github_client, get_repo, get_file_tree
        
        token = env_vars.get("GITHUB_TEST_TOKEN", "")
        if not token:
            # Try without token for public repos
            from github import Github
            client = Github()
        else:
            client = get_github_client(token)
        
        repo = get_repo(client, TEST_REPO_URL)
        file_tree = get_file_tree(repo)
        
        if not file_tree:
            raise Exception("File tree is empty")
        
        return {
            "repo": repo.full_name,
            "file_count": len(file_tree),
            "sample_files": file_tree[:5],
        }
    
    await test_github_fetch()
    
    # ── Test 4: Full Planner Node ─────────────────────────────────────────────
    @harness.test("Full Planner Node")
    async def test_full_planner():
        """Run the complete planner_node with real services."""
        from backend.agents.planner import planner_node
        from unittest.mock import AsyncMock, patch
        
        state = get_test_state_minimal()
        config = get_test_config(env_vars.get("GITHUB_TEST_TOKEN"))
        
        # Mock only Supabase calls to avoid polluting the real database
        with (
            patch("backend.agents.planner.update_run_status", new=AsyncMock()),
            patch("backend.agents.planner.save_agent_output", new=AsyncMock()),
        ):
            result = await planner_node(state, config)
        
        if result.get("error"):
            raise Exception(f"Planner returned error: {result['error']}")
        
        subtasks = result.get("subtasks", [])
        if not subtasks:
            raise Exception("Planner produced no subtasks")
        
        return {
            "subtask_count": len(subtasks),
            "subtask_titles": [st.get("title", st.get("id", "?"))[:50] for st in subtasks[:3]],
            "repo_tree_count": len(result.get("repo_tree", [])),
        }
    
    await test_full_planner()
    
    harness.finish_agent()
    return harness.current_agent is None or harness.agent_results[-1].all_passed


async def main():
    """Run planner tests standalone."""
    print("=" * 60)
    print("PRISM BACKEND AGENT TEST HARNESS")
    print("Testing: Planner Agent")
    print("=" * 60)
    
    env_vars = validate_env()
    print_env_summary(env_vars)
    
    harness = TestHarness(verbose=True)
    
    success = await run_planner_tests(harness)
    
    harness.print_summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
