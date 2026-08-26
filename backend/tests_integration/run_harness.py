"""
backend/tests_integration/run_harness.py — CLI entry point for the test harness.

Usage:
    # Run all agent tests
    python -m backend.tests_integration.run_harness --all
    
    # Run tests for a specific agent
    python -m backend.tests_integration.run_harness --agent planner
    python -m backend.tests_integration.run_harness --agent code_navigator
    python -m backend.tests_integration.run_harness --agent impl_planner
    python -m backend.tests_integration.run_harness --agent test_runner
    python -m backend.tests_integration.run_harness --agent debugger
    python -m backend.tests_integration.run_harness --agent pr_summarizer
    
    # Run full pipeline test (all agents in sequence)
    python -m backend.tests_integration.run_harness --pipeline
    
    # Quick test (planner only - good for first-time setup)
    python -m backend.tests_integration.run_harness --quick
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.tests_integration.harness import (
    TestHarness,
    validate_env,
    print_env_summary,
)


AGENT_NAMES = [
    "planner",
    "code_navigator",
    "impl_planner",
    "test_runner",
    "debugger",
    "pr_summarizer",
]


async def run_agent_tests(harness: TestHarness, agent: str) -> bool:
    """Run tests for a specific agent."""
    if agent == "planner":
        from backend.tests_integration.test_planner_live import run_planner_tests
        return await run_planner_tests(harness)
    elif agent == "code_navigator":
        from backend.tests_integration.test_code_navigator_live import run_code_navigator_tests
        return await run_code_navigator_tests(harness)
    elif agent == "impl_planner":
        from backend.tests_integration.test_impl_planner_live import run_impl_planner_tests
        return await run_impl_planner_tests(harness)
    elif agent == "test_runner":
        from backend.tests_integration.test_test_runner_live import run_test_runner_tests
        run_full = os.getenv("PRISM_TEST_RUNNER_FULL", "").lower() in ("1", "true", "yes")
        return await run_test_runner_tests(harness, run_full=run_full)
    elif agent == "debugger":
        from backend.tests_integration.test_debugger_live import run_debugger_tests
        return await run_debugger_tests(harness)
    elif agent == "pr_summarizer":
        from backend.tests_integration.test_pr_summarizer_live import run_pr_summarizer_tests
        return await run_pr_summarizer_tests(harness)
    else:
        print(f"Unknown agent: {agent}")
        print(f"Available agents: {', '.join(AGENT_NAMES)}")
        return False


async def run_all_tests(harness: TestHarness) -> bool:
    """Run tests for all agents."""
    all_passed = True
    
    for agent in AGENT_NAMES:
        try:
            passed = await run_agent_tests(harness, agent)
            if not passed:
                all_passed = False
        except Exception as e:
            print(f"\nFATAL ERROR in {agent} tests: {e}")
            all_passed = False
    
    return all_passed


async def run_pipeline_test(harness: TestHarness) -> bool:
    """
    Run a full pipeline test simulating the entire flow.
    
    This test runs agents in sequence, passing state from one to the next,
    simulating a real run without HITL checkpoints.
    """
    from unittest.mock import AsyncMock, patch
    from backend.tests_integration.harness import (
        get_test_state_minimal,
        get_test_config,
        TEST_REPO_URL,
        TEST_ISSUE_TEXT,
    )
    
    env_vars = validate_env()
    config = get_test_config(env_vars.get("GITHUB_TEST_TOKEN"))
    
    print("\n" + "=" * 60)
    print("FULL PIPELINE TEST")
    print("=" * 60)
    print(f"\nRepository: {TEST_REPO_URL}")
    print(f"Issue: {TEST_ISSUE_TEXT[:100]}...")
    print()
    
    harness.start_agent("pipeline")
    
    # Mock all Supabase writes
    mock_patches = {
        "backend.agents.planner.update_run_status": AsyncMock(),
        "backend.agents.planner.save_agent_output": AsyncMock(),
        "backend.agents.code_navigator.update_run_status": AsyncMock(),
        "backend.agents.code_navigator.save_agent_output": AsyncMock(),
        "backend.agents.code_navigator.save_code_embeddings": AsyncMock(),
        "backend.agents.code_navigator.upsert_repo_cache": AsyncMock(),
        "backend.agents.implementation_planner.update_run_status": AsyncMock(),
        "backend.agents.implementation_planner.save_agent_output": AsyncMock(),
        "backend.agents.debugger.update_run_status": AsyncMock(),
        "backend.agents.debugger.save_agent_output": AsyncMock(),
        "backend.agents.pr_summarizer.update_run_status": AsyncMock(),
        "backend.agents.pr_summarizer.save_agent_output": AsyncMock(),
    }
    
    state = get_test_state_minimal()
    
    @harness.test("Planner -> Code Navigator -> Impl Planner -> PR Summarizer")
    async def test_pipeline():
        nonlocal state
        
        with patch.multiple("", **{k: v for k, v in mock_patches.items()}):
            # 1. Planner
            print("  [1/4] Running Planner...")
            from backend.agents.planner import planner_node
            with (
                patch("backend.agents.planner.update_run_status", new=AsyncMock()),
                patch("backend.agents.planner.save_agent_output", new=AsyncMock()),
            ):
                result = await planner_node(state, config)
            if result.get("error"):
                raise Exception(f"Planner failed: {result['error']}")
            state.update(result)
            print(f"       -> {len(state['subtasks'])} subtasks")
            
            # 2. Code Navigator
            print("  [2/4] Running Code Navigator...")
            from backend.agents.code_navigator import code_navigator_node
            with (
                patch("backend.agents.code_navigator.update_run_status", new=AsyncMock()),
                patch("backend.agents.code_navigator.save_agent_output", new=AsyncMock()),
                patch("backend.agents.code_navigator.save_code_embeddings", new=AsyncMock()),
                patch("backend.agents.code_navigator.upsert_repo_cache", new=AsyncMock()),
            ):
                result = await code_navigator_node(state, config)
            if result.get("error"):
                raise Exception(f"Code Navigator failed: {result['error']}")
            state.update(result)
            print(f"       -> {len(state['file_contents'])} files fetched")
            
            # 3. Implementation Planner
            print("  [3/4] Running Implementation Planner...")
            from backend.agents.implementation_planner import implementation_planner_node
            with (
                patch("backend.agents.implementation_planner.update_run_status", new=AsyncMock()),
                patch("backend.agents.implementation_planner.save_agent_output", new=AsyncMock()),
            ):
                result = await implementation_planner_node(state)
            if result.get("error"):
                raise Exception(f"Implementation Planner failed: {result['error']}")
            state.update(result)
            total_steps = sum(len(p.get("steps", [])) for p in state.get("implementation_plan", []))
            print(f"       -> {total_steps} implementation steps")
            
            # Skip Test Runner (expensive) - simulate passing tests
            state["test_results"] = {
                "framework": "pytest",
                "passed": ["simulated_test"],
                "failed": [],
                "passed_count": 1,
                "failed_count": 0,
                "exit_code": 0,
                "stdout": "1 passed",
                "stderr": "",
            }
            state["all_tests_passed"] = True
            
            # 4. PR Summarizer
            print("  [4/4] Running PR Summarizer...")
            from backend.agents.pr_summarizer import pr_summarizer_node
            with (
                patch("backend.agents.pr_summarizer.update_run_status", new=AsyncMock()),
                patch("backend.agents.pr_summarizer.save_agent_output", new=AsyncMock()),
            ):
                result = await pr_summarizer_node(state)
            if result.get("error"):
                raise Exception(f"PR Summarizer failed: {result['error']}")
            state.update(result)
            
            pr_draft = state.get("pr_draft", {})
            
            return {
                "subtasks": len(state.get("subtasks", [])),
                "files_mapped": len(state.get("file_map", {})),
                "pr_title": pr_draft.get("title", "")[:50],
            }
    
    await test_pipeline()
    
    harness.finish_agent()
    return harness.agent_results[-1].all_passed


async def main():
    parser = argparse.ArgumentParser(
        description="Prism Backend Agent Test Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m backend.tests_integration.run_harness --quick    # Quick test (planner only)
  python -m backend.tests_integration.run_harness --all      # Run all agent tests
  python -m backend.tests_integration.run_harness --agent planner
  python -m backend.tests_integration.run_harness --pipeline # Full pipeline test
        """
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Run all agent tests")
    group.add_argument("--agent", type=str, choices=AGENT_NAMES, help="Run tests for a specific agent")
    group.add_argument("--pipeline", action="store_true", help="Run full pipeline test")
    group.add_argument("--quick", action="store_true", help="Quick test (planner only)")
    
    parser.add_argument("--verbose", "-v", action="store_true", default=True, help="Verbose output")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("PRISM BACKEND AGENT TEST HARNESS")
    print("=" * 60)
    
    env_vars = validate_env()
    print_env_summary(env_vars)
    
    harness = TestHarness(verbose=args.verbose)
    
    if args.quick:
        print("\nMode: Quick Test (Planner Only)")
        success = await run_agent_tests(harness, "planner")
    elif args.agent:
        print(f"\nMode: Single Agent ({args.agent})")
        success = await run_agent_tests(harness, args.agent)
    elif args.pipeline:
        print("\nMode: Full Pipeline Test")
        success = await run_pipeline_test(harness)
    else:  # --all
        print("\nMode: All Agent Tests")
        success = await run_all_tests(harness)
    
    harness.print_summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
