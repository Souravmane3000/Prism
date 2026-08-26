"""
backend/tests_integration/test_code_navigator_live.py — Live integration tests for Code Navigator agent.

Tests:
1. Supabase Connection - Verify Supabase client connects
2. pgvector RPC - Verify semantic search function works
3. GitHub File Fetch - Verify file content retrieval
4. Full Code Navigator - Run code_navigator_node with real inputs

Run standalone:
    python -m backend.tests_integration.test_code_navigator_live
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
    get_test_config,
    TEST_REPO_URL,
)


async def run_code_navigator_tests(harness: TestHarness) -> bool:
    """Run all code navigator agent tests."""
    
    env_vars = validate_env()
    harness.start_agent("code_navigator")
    
    # ── Test 1: Supabase Connection ───────────────────────────────────────────
    @harness.test("Supabase Connection")
    async def test_supabase_connection():
        """Verify Supabase client can connect and query."""
        from backend.supabase_client import get_supabase
        import asyncio
        
        client = get_supabase()
        
        # Simple query to verify connection (may return empty)
        result = await asyncio.to_thread(
            lambda: client.table("runs").select("id").limit(1).execute()
        )
        
        return {
            "connected": True,
            "table_accessible": True,
        }
    
    await test_supabase_connection()
    
    # ── Test 2: pgvector RPC ──────────────────────────────────────────────────
    @harness.test("pgvector Semantic Search")
    async def test_pgvector_rpc():
        """Verify the match_code_embeddings RPC function exists and responds."""
        from backend.supabase_client import search_code_embeddings
        
        # Create a dummy query vector (1536 dimensions for OpenAI embeddings)
        dummy_vector = [0.0] * 1536
        
        try:
            # This may return empty results, but should not error
            results = await search_code_embeddings(
                repo_url=TEST_REPO_URL,
                query_vector=dummy_vector,
                limit=5,
            )
            
            return {
                "rpc_callable": True,
                "result_count": len(results),
            }
        except Exception as e:
            if "function" in str(e).lower() and "does not exist" in str(e).lower():
                raise Exception(
                    "The match_code_embeddings RPC function does not exist. "
                    "Run the Supabase migration to create it."
                )
            raise
    
    await test_pgvector_rpc()
    
    # ── Test 3: GitHub File Content Fetch ─────────────────────────────────────
    @harness.test("GitHub File Content Fetch")
    async def test_github_file_fetch():
        """Verify GitHub file content retrieval works."""
        from backend.github_client import get_github_client, get_repo, get_file_content
        
        token = env_vars.get("GITHUB_TEST_TOKEN", "")
        if not token:
            from github import Github
            client = Github()
        else:
            client = get_github_client(token)
        
        repo = get_repo(client, TEST_REPO_URL)
        
        # Fetch a known file from fastapi repo
        content = get_file_content(repo, "README.md")
        
        if not content:
            raise Exception("Failed to fetch README.md content")
        
        return {
            "file": "README.md",
            "content_length": len(content),
            "preview": content[:100].replace("\n", " "),
        }
    
    await test_github_file_fetch()
    
    # ── Test 4: Full Code Navigator Node ──────────────────────────────────────
    @harness.test("Full Code Navigator Node")
    async def test_full_code_navigator():
        """Run the complete code_navigator_node with real services."""
        from backend.agents.code_navigator import code_navigator_node
        from unittest.mock import AsyncMock, patch
        
        # Need subtasks from planner - use minimal sample
        state = get_test_state_minimal()
        state["subtasks"] = [
            {
                "id": "st-1",
                "title": "Add custom exception handlers",
                "description": "Implement exception handler support in dependency injection",
                "dependencies": [],
                "likely_files": ["fastapi/dependencies/utils.py"],
                "complexity": "medium",
            }
        ]
        state["repo_tree"] = [
            "fastapi/__init__.py",
            "fastapi/applications.py",
            "fastapi/dependencies/__init__.py",
            "fastapi/dependencies/utils.py",
            "fastapi/routing.py",
        ]
        
        config = get_test_config(env_vars.get("GITHUB_TEST_TOKEN"))
        
        # Mock Supabase writes but allow reads (for pgvector search)
        with (
            patch("backend.agents.code_navigator.update_run_status", new=AsyncMock()),
            patch("backend.agents.code_navigator.save_agent_output", new=AsyncMock()),
            patch("backend.agents.code_navigator.save_code_embeddings", new=AsyncMock()),
            patch("backend.agents.code_navigator.upsert_repo_cache", new=AsyncMock()),
        ):
            result = await code_navigator_node(state, config)
        
        if result.get("error"):
            raise Exception(f"Code Navigator returned error: {result['error']}")
        
        file_map = result.get("file_map", {})
        file_contents = result.get("file_contents", {})
        
        return {
            "subtasks_mapped": len(file_map),
            "files_fetched": len(file_contents),
            "sample_files": list(file_contents.keys())[:3],
        }
    
    await test_full_code_navigator()
    
    harness.finish_agent()
    return harness.agent_results[-1].all_passed


async def main():
    """Run code navigator tests standalone."""
    print("=" * 60)
    print("PRISM BACKEND AGENT TEST HARNESS")
    print("Testing: Code Navigator Agent")
    print("=" * 60)
    
    env_vars = validate_env()
    print_env_summary(env_vars)
    
    harness = TestHarness(verbose=True)
    
    success = await run_code_navigator_tests(harness)
    
    harness.print_summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
