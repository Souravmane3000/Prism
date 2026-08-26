"""
backend/tests_integration/harness.py — Core test harness utilities.

Provides:
- Environment loading with validation
- Test result tracking with timing
- Detailed error formatting and diagnosis
- Common test fixtures for real services
"""

import asyncio
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class TestStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class TestResult:
    """Result of a single test case."""
    name: str
    status: TestStatus
    duration_ms: float
    message: Optional[str] = None
    error: Optional[Exception] = None
    traceback: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTestResults:
    """Results for all tests of a single agent."""
    agent_name: str
    tests: list[TestResult] = field(default_factory=list)
    
    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.PASS)
    
    @property
    def failed(self) -> int:
        return sum(1 for t in self.tests if t.status == TestStatus.FAIL)
    
    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and len(self.tests) > 0


class TestHarness:
    """
    Core test harness for running integration tests.
    
    Usage:
        harness = TestHarness()
        harness.start_agent("planner")
        
        @harness.test("LLM Connection")
        async def test_llm():
            ...
        
        await test_llm()
        harness.finish_agent()
        harness.print_summary()
    """
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.agent_results: list[AgentTestResults] = []
        self.current_agent: Optional[AgentTestResults] = None
        self.start_time: Optional[float] = None
    
    def start_agent(self, agent_name: str) -> None:
        """Begin testing a new agent."""
        self.current_agent = AgentTestResults(agent_name=agent_name)
        if self.verbose:
            idx = len(self.agent_results) + 1
            print(f"\n[{idx}/6] Testing {agent_name.replace('_', ' ').title()} Agent...")
    
    def finish_agent(self) -> None:
        """Finish testing current agent and store results."""
        if self.current_agent:
            self.agent_results.append(self.current_agent)
            self.current_agent = None
    
    def test(self, name: str):
        """Decorator for test functions with timing and error capture."""
        def decorator(func: Callable):
            async def wrapper(*args, **kwargs):
                start = time.perf_counter()
                result = TestResult(name=name, status=TestStatus.PASS, duration_ms=0)
                
                try:
                    return_value = await func(*args, **kwargs)
                    result.duration_ms = (time.perf_counter() - start) * 1000
                    result.status = TestStatus.PASS
                    
                    if isinstance(return_value, dict):
                        result.details = return_value
                    
                    if self.verbose:
                        self._print_result(result)
                    
                    if self.current_agent:
                        self.current_agent.tests.append(result)
                    
                    return return_value
                    
                except Exception as e:
                    result.duration_ms = (time.perf_counter() - start) * 1000
                    result.status = TestStatus.FAIL
                    result.error = e
                    result.traceback = traceback.format_exc()
                    result.message = str(e)
                    
                    if self.verbose:
                        self._print_result(result)
                    
                    if self.current_agent:
                        self.current_agent.tests.append(result)
                    
                    return None
            
            return wrapper
        return decorator
    
    def _print_result(self, result: TestResult) -> None:
        """Print a single test result."""
        status_str = result.status.value
        duration_str = f"({result.duration_ms:.0f}ms)"
        
        if result.status == TestStatus.PASS:
            print(f"  - {result.name}: {status_str} {duration_str}")
            if result.details:
                for key, value in result.details.items():
                    if isinstance(value, (int, str)):
                        print(f"    -> {key}: {value}")
        else:
            print(f"  - {result.name}: {status_str}")
            print()
            print("    ERROR DETAILS:")
            print("    " + "-" * 50)
            if result.error:
                print(f"    Exception: {type(result.error).__name__}")
                print(f"    Message: {result.message}")
            print()
            if result.traceback:
                for line in result.traceback.split("\n")[-10:]:
                    print(f"    {line}")
            print()
            
            # Provide diagnosis for common errors
            diagnosis = self._diagnose_error(result.error)
            if diagnosis:
                print("    DIAGNOSIS:")
                print("    " + "-" * 50)
                for line in diagnosis.split("\n"):
                    print(f"    {line}")
                print()
    
    def _diagnose_error(self, error: Optional[Exception]) -> Optional[str]:
        """Provide human-readable diagnosis for common errors."""
        if error is None:
            return None
        
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # LLM/OpenAI authentication errors
        if "401" in error_str or "unauthorized" in error_str:
            return (
                "OpenAI rejected the API key (401 Unauthorized).\n\n"
                "FIX: Update OPENAI_API_KEY in .env with a valid key from\n"
                "https://platform.openai.com/api-keys. The key must have\n"
                "access to gpt-4o-mini and text-embedding-3-small."
            )
        
        # Supabase connection errors
        if "supabase" in error_str or "postgresql" in error_str:
            if "password authentication failed" in error_str:
                return (
                    "Supabase database authentication failed.\n\n"
                    "FIX: Verify SUPABASE_DB_URL in .env has the correct password."
                )
            if "connection refused" in error_str:
                return (
                    "Cannot connect to Supabase database.\n\n"
                    "FIX: Check SUPABASE_URL in .env and ensure Supabase is running."
                )
        
        # GitHub rate limit
        if "rate limit" in error_str or "403" in error_str:
            return (
                "GitHub API rate limit exceeded or authentication failed.\n\n"
                "FIX: Ensure GITHUB_TEST_TOKEN in .env is a valid PAT with\n"
                "repo read permissions."
            )
        
        # JSON parsing errors
        if "json" in error_str or "JSONDecodeError" in error_type:
            return (
                "LLM returned invalid JSON. This may indicate:\n"
                "1. The LLM is returning markdown-wrapped JSON\n"
                "2. The prompt needs adjustment\n"
                "3. Temperature is too high\n\n"
                "FIX: Check the raw LLM response in the error details above."
            )
        
        # Timeout errors
        if "timeout" in error_str or "TimeoutError" in error_type:
            return (
                "Request timed out. The service may be slow or unavailable.\n\n"
                "FIX: Try again or increase timeout in backend/llm.py"
            )
        
        return None
    
    def print_summary(self) -> None:
        """Print final summary of all tests."""
        print()
        print("=" * 60)
        
        total_passed = sum(r.passed for r in self.agent_results)
        total_failed = sum(r.failed for r in self.agent_results)
        total_agents_passed = sum(1 for r in self.agent_results if r.all_passed)
        
        if total_failed == 0:
            print(f"SUMMARY: All {total_passed} tests passed across {len(self.agent_results)} agents")
        else:
            print(f"SUMMARY: {total_passed} passed, {total_failed} failed")
            print()
            print("FAILED TESTS:")
            for agent_result in self.agent_results:
                for test in agent_result.tests:
                    if test.status == TestStatus.FAIL:
                        print(f"  - [{agent_result.agent_name}] {test.name}")
        
        print("=" * 60)
    
    @property
    def all_passed(self) -> bool:
        return all(r.all_passed for r in self.agent_results)


# ── Environment Validation ────────────────────────────────────────────────────

def validate_env() -> dict[str, str]:
    """
    Validate all required environment variables are set.
    Returns dict of env vars or raises with detailed error.
    """
    required = {
        "OPENAI_API_KEY": "OpenAI API key for gpt-4o-mini and embeddings",
        "SUPABASE_URL": "Supabase project URL",
        "SUPABASE_SERVICE_KEY": "Supabase service role key",
        "SUPABASE_DB_URL": "Supabase PostgreSQL connection URL",
    }
    
    optional = {
        "OPENAI_MODEL_NAME": "Chat model (default: gpt-4o-mini)",
        "OPENAI_EMBEDDING_MODEL": "Embedding model (default: text-embedding-3-small)",
        "GITHUB_TEST_TOKEN": "GitHub PAT for testing (optional but recommended)",
        "LANGSMITH_API_KEY": "LangSmith tracing key (optional)",
    }
    
    missing = []
    env_vars = {}
    
    for var, description in required.items():
        value = os.getenv(var)
        if not value:
            missing.append(f"  - {var}: {description}")
        else:
            env_vars[var] = value
    
    if missing:
        print("ERROR: Missing required environment variables:")
        for m in missing:
            print(m)
        print()
        print("Ensure these are set in your .env file at the project root.")
        sys.exit(1)
    
    # Add optional vars
    for var, description in optional.items():
        value = os.getenv(var)
        if value:
            env_vars[var] = value
    
    return env_vars


def print_env_summary(env_vars: dict[str, str]) -> None:
    """Print summary of loaded environment (with secrets redacted)."""
    print("Environment loaded:")
    for var, value in env_vars.items():
        if "KEY" in var or "TOKEN" in var or "SECRET" in var:
            redacted = f"{value[:8]}...{value[-4:]}" if len(value) > 12 else "***"
            print(f"  - {var}: {redacted}")
        else:
            print(f"  - {var}: {value[:50]}...")


# ── Test Fixtures ─────────────────────────────────────────────────────────────

# Standard test repository (public, stable)
TEST_REPO_URL = "https://github.com/tiangolo/fastapi"
TEST_REPO_OWNER = "tiangolo"
TEST_REPO_NAME = "fastapi"

# Standard test issue (static text to avoid GitHub API)
TEST_ISSUE_TEXT = """
Add support for custom exception handlers in dependency injection.

When a dependency raises a custom exception, the current behavior is to
return a 500 Internal Server Error. It would be useful to allow users
to register exception handlers that can catch exceptions from dependencies
and return custom responses.

Acceptance criteria:
1. Add an `exception_handlers` parameter to `Depends()`
2. Document the new feature in the dependency injection guide
3. Add unit tests for the new functionality
"""

# Standard test run ID
TEST_RUN_ID = "test-harness-00000000-0000-0000-0000-000000000001"


def get_test_state_minimal() -> dict[str, Any]:
    """Return minimal PrismState for planner testing."""
    return {
        "repo_url": TEST_REPO_URL,
        "issue_url": None,
        "issue_text": TEST_ISSUE_TEXT,
        "run_id": TEST_RUN_ID,
        "repo_tree": [],
        "subtasks": [],
        "planner_approved": False,
        "file_map": {},
        "file_contents": {},
        "implementation_plan": [],
        "impl_approved": False,
        "test_results": None,
        "all_tests_passed": False,
        "debug_report": None,
        "pr_draft": None,
        "current_agent": "planner",
        "error": None,
        "messages": [],
    }


def get_test_config(github_token: Optional[str] = None) -> dict[str, Any]:
    """Return LangGraph config with test settings."""
    token = github_token or os.getenv("GITHUB_TEST_TOKEN", "")
    return {
        "configurable": {
            "thread_id": TEST_RUN_ID,
            "github_token": token,
        }
    }


# ── Async Utilities ───────────────────────────────────────────────────────────

def run_async(coro):
    """Run an async coroutine in a new event loop."""
    return asyncio.run(coro)
