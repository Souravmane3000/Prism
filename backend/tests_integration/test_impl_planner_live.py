"""
backend/tests_integration/test_impl_planner_live.py — Live integration tests for Implementation Planner agent.

Tests:
1. LLM Plan Generation - Verify LLM can generate implementation plans
2. Full Impl Planner - Run implementation_planner_node with real inputs

Run standalone:
    python -m backend.tests_integration.test_impl_planner_live
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
    TEST_RUN_ID,
)


async def run_impl_planner_tests(harness: TestHarness) -> bool:
    """Run all implementation planner agent tests."""
    
    env_vars = validate_env()
    harness.start_agent("impl_planner")
    
    # ── Test 1: LLM Plan Generation ───────────────────────────────────────────
    @harness.test("LLM Plan Generation")
    async def test_llm_plan_generation():
        """Verify LLM can generate a structured implementation plan."""
        from backend.llm import get_llm
        from langchain_core.messages import SystemMessage, HumanMessage
        
        llm = get_llm(temperature=0.1)
        
        system_prompt = """You are a senior software engineer. Given a subtask and relevant code,
produce a JSON array of implementation steps. Return ONLY valid JSON.

Schema for each step:
{
  "order": 1,
  "file": "path/to/file.py",
  "function_or_symbol": "function_name",
  "change_description": "What to change",
  "rationale": "Why this change",
  "tradeoffs": ["Any tradeoffs"]
}"""

        user_prompt = """Subtask: Add rate limiting middleware
        
Relevant file (backend/main.py):
```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "World"}
```

Produce 2-3 implementation steps as a JSON array."""

        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        
        content = str(response.content).strip()
        
        # Try to parse as JSON
        import json
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        
        steps = json.loads(content)
        
        if not isinstance(steps, list) or len(steps) == 0:
            raise Exception(f"Expected JSON array of steps, got: {content[:200]}")
        
        return {
            "step_count": len(steps),
            "first_step_file": steps[0].get("file", "unknown"),
        }
    
    await test_llm_plan_generation()
    
    # ── Test 2: Full Implementation Planner Node ──────────────────────────────
    @harness.test("Full Implementation Planner Node")
    async def test_full_impl_planner():
        """Run the complete implementation_planner_node with real services."""
        from backend.agents.implementation_planner import implementation_planner_node
        from unittest.mock import AsyncMock, patch
        
        # Build state with subtasks and file contents
        state = get_test_state_minimal()
        state["subtasks"] = [
            {
                "id": "st-1",
                "title": "Add rate limiting middleware",
                "description": "Implement request rate limiting using SlowAPI",
                "dependencies": [],
                "likely_files": ["backend/main.py"],
                "complexity": "medium",
            }
        ]
        state["file_map"] = {
            "st-1": [
                {"path": "backend/main.py", "relevance_score": 0.95, "source": "both"}
            ]
        }
        state["file_contents"] = {
            "backend/main.py": """from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "World"}
"""
        }
        
        # Mock Supabase calls
        with (
            patch("backend.agents.implementation_planner.update_run_status", new=AsyncMock()),
            patch("backend.agents.implementation_planner.save_agent_output", new=AsyncMock()),
        ):
            result = await implementation_planner_node(state)
        
        if result.get("error"):
            raise Exception(f"Implementation Planner returned error: {result['error']}")
        
        impl_plan = result.get("implementation_plan", [])
        if not impl_plan:
            raise Exception("Implementation Planner produced no plan")
        
        total_steps = sum(len(item.get("steps", [])) for item in impl_plan)
        
        return {
            "subtasks_planned": len(impl_plan),
            "total_steps": total_steps,
            "first_subtask_id": impl_plan[0].get("subtask_id") if impl_plan else "none",
        }
    
    await test_full_impl_planner()
    
    harness.finish_agent()
    return harness.agent_results[-1].all_passed


async def main():
    """Run implementation planner tests standalone."""
    print("=" * 60)
    print("PRISM BACKEND AGENT TEST HARNESS")
    print("Testing: Implementation Planner Agent")
    print("=" * 60)
    
    env_vars = validate_env()
    print_env_summary(env_vars)
    
    harness = TestHarness(verbose=True)
    
    success = await run_impl_planner_tests(harness)
    
    harness.print_summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
