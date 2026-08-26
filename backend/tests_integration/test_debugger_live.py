"""
backend/tests_integration/test_debugger_live.py — Live integration tests for Debugger agent.

Tests:
1. LLM Debug Analysis - Verify LLM can analyze test failures
2. Full Debugger Node - Run debugger_node with sample failures

Run standalone:
    python -m backend.tests_integration.test_debugger_live
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


async def run_debugger_tests(harness: TestHarness) -> bool:
    """Run all debugger agent tests."""
    
    env_vars = validate_env()
    harness.start_agent("debugger")
    
    # ── Test 1: LLM Debug Analysis ────────────────────────────────────────────
    @harness.test("LLM Debug Analysis")
    async def test_llm_debug_analysis():
        """Verify LLM can analyze a test failure and propose fixes."""
        from backend.llm import get_llm
        from langchain_core.messages import SystemMessage, HumanMessage
        
        llm = get_llm(temperature=0.1)
        
        system_prompt = """You are a senior software engineer debugging test failures.
Given a test failure and the relevant code, identify the root cause and propose a fix.
Return ONLY valid JSON with this schema:
{
  "root_cause": "Brief explanation of why the test failed",
  "proposed_fix": "Description of the fix",
  "confidence": 0.0-1.0,
  "target_file": "path/to/file.py"
}"""

        user_prompt = """Test Failure:
test_rate_limiter.py::test_rate_limit_exceeded - AssertionError: Expected 429, got 200

Traceback:
    def test_rate_limit_exceeded():
        for _ in range(10):
            client.get("/api")
>       response = client.get("/api")
>       assert response.status_code == 429
E       AssertionError: assert 200 == 429

Relevant code (backend/middleware.py):
```python
from slowapi import Limiter
limiter = Limiter(key_func=lambda: "global")

# Rate limit not applied to route
@app.get("/api")
def api_endpoint():
    return {"status": "ok"}
```

Analyze this failure and return JSON."""

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
        
        if "root_cause" not in result:
            raise Exception(f"Missing root_cause in response: {content[:200]}")
        
        return {
            "has_root_cause": bool(result.get("root_cause")),
            "has_fix": bool(result.get("proposed_fix")),
            "confidence": result.get("confidence", 0),
        }
    
    await test_llm_debug_analysis()
    
    # ── Test 2: Full Debugger Node ────────────────────────────────────────────
    @harness.test("Full Debugger Node")
    async def test_full_debugger():
        """Run the complete debugger_node with sample test failures."""
        from backend.agents.debugger import debugger_node
        from unittest.mock import AsyncMock, patch
        
        state = get_test_state_minimal()
        state["test_results"] = {
            "framework": "pytest",
            "passed": ["test_health"],
            "failed": [
                {
                    "name": "test_rate_limiter.py::test_rate_limit_exceeded",
                    "message": "AssertionError: Expected 429, got 200",
                    "traceback": """def test_rate_limit_exceeded():
    for _ in range(10):
        client.get("/api")
    response = client.get("/api")
>   assert response.status_code == 429
E   AssertionError: assert 200 == 429""",
                }
            ],
            "passed_count": 1,
            "failed_count": 1,
            "exit_code": 1,
            "stdout": "1 passed, 1 failed",
            "stderr": "",
        }
        state["file_map"] = {
            "st-1": [{"path": "backend/middleware.py", "relevance_score": 0.9, "source": "both"}]
        }
        state["file_contents"] = {
            "backend/middleware.py": """from slowapi import Limiter
limiter = Limiter(key_func=lambda: "global")

@app.get("/api")
def api_endpoint():
    return {"status": "ok"}
"""
        }
        state["implementation_plan"] = [
            {
                "subtask_id": "st-1",
                "steps": [
                    {"order": 1, "file": "backend/middleware.py", "change_description": "Add rate limiter"}
                ]
            }
        ]
        
        # Mock Supabase calls
        with (
            patch("backend.agents.debugger.update_run_status", new=AsyncMock()),
            patch("backend.agents.debugger.save_agent_output", new=AsyncMock()),
        ):
            result = await debugger_node(state)
        
        # Debugger doesn't fail on errors - it produces a partial report
        debug_report = result.get("debug_report", {})
        
        return {
            "has_summary": bool(debug_report.get("summary")),
            "fix_count": len(debug_report.get("fixes", [])),
        }
    
    await test_full_debugger()
    
    harness.finish_agent()
    return harness.agent_results[-1].all_passed


async def main():
    """Run debugger tests standalone."""
    print("=" * 60)
    print("PRISM BACKEND AGENT TEST HARNESS")
    print("Testing: Debugger Agent")
    print("=" * 60)
    
    env_vars = validate_env()
    print_env_summary(env_vars)
    
    harness = TestHarness(verbose=True)
    
    success = await run_debugger_tests(harness)
    
    harness.print_summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
