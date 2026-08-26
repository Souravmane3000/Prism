"""
tests/conftest.py — Shared pytest fixtures for the full Prism test suite.

All fixtures use unittest.mock so no real credentials are needed.
The mock_settings fixture is applied automatically for every test via
the monkeypatching of backend.config.settings.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Patch settings before any backend module imports them ─────────────────────
# This must happen before the first import of any backend module.

os.environ.setdefault("OPENAI_API_KEY", "sk-fakeopenai-key-for-tests")
os.environ.setdefault("OPENAI_MODEL_NAME", "gpt-4o-mini")
os.environ.setdefault("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
os.environ.setdefault("LANGSMITH_API_KEY", "lsv2_pt_fake")
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake-service-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake-anon-key")
os.environ.setdefault("SUPABASE_DB_URL", "postgresql://user:pass@localhost:5432/postgres")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:5000")


# ── Settings fixture ──────────────────────────────────────────────────────────

@pytest.fixture()
def mock_settings():
    """Patch backend.config.settings with test-safe values."""
    settings_mock = MagicMock()
    settings_mock.openai_api_key = "sk-fakeopenai-key-for-tests"
    settings_mock.openai_model_name = "gpt-4o-mini"
    settings_mock.openai_embedding_model = "text-embedding-3-small"
    settings_mock.langsmith_api_key = "lsv2_pt_fake"
    settings_mock.langchain_project = "prism"
    settings_mock.supabase_url = "https://fake.supabase.co"
    settings_mock.supabase_service_key = "fake-service-key"
    settings_mock.supabase_anon_key = "fake-anon-key"
    settings_mock.supabase_db_url = "postgresql://user:pass@localhost:5432/postgres"
    settings_mock.environment = "development"
    settings_mock.frontend_origin = "http://localhost:5000"
    with patch("backend.config.settings", settings_mock):
        yield settings_mock


# ── Sample state fixture ──────────────────────────────────────────────────────

@pytest.fixture()
def sample_subtasks() -> list[dict[str, Any]]:
    return [
        {
            "id": "st-1",
            "title": "Add rate limiting to API",
            "description": "Implement request rate limiting on the /api endpoint",
            "dependencies": [],
            "likely_files": ["backend/main.py", "backend/middleware.py"],
            "complexity": "medium",
        },
        {
            "id": "st-2",
            "title": "Write tests for rate limiter",
            "description": "Add pytest tests for the new rate limiting middleware",
            "dependencies": ["st-1"],
            "likely_files": ["tests/test_middleware.py"],
            "complexity": "low",
        },
    ]


@pytest.fixture()
def sample_implementation_plan() -> list[dict[str, Any]]:
    return [
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
            ],
        }
    ]


@pytest.fixture()
def sample_test_results() -> dict[str, Any]:
    return {
        "framework": "pytest",
        "passed": ["test_health", "test_start_run"],
        "failed": [],
        "passed_count": 2,
        "failed_count": 0,
        "exit_code": 0,
        "stdout": "2 passed in 0.42s",
        "stderr": "",
    }


@pytest.fixture()
def sample_state(sample_subtasks, sample_implementation_plan, sample_test_results) -> dict[str, Any]:
    """A fully-populated PrismState dict for agent node tests."""
    return {
        "repo_url": "https://github.com/test-org/test-repo",
        "issue_url": "https://github.com/test-org/test-repo/issues/42",
        "issue_text": "Add rate limiting to the public API to prevent abuse",
        "run_id": "run-00000000-0000-0000-0000-000000000001",
        "repo_tree": ["backend/main.py", "backend/middleware.py", "tests/test_middleware.py"],
        "subtasks": sample_subtasks,
        "planner_approved": True,
        "file_map": {
            "st-1": [
                {"path": "backend/main.py", "relevance_score": 0.92, "source": "pgvector"}
            ]
        },
        "file_contents": {
            "backend/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"
        },
        "implementation_plan": sample_implementation_plan,
        "impl_approved": True,
        "test_results": sample_test_results,
        "all_tests_passed": True,
        "debug_report": None,
        "pr_draft": None,
        "current_agent": "planner",
        "error": None,
        "messages": ["[planner] Starting"],
    }


@pytest.fixture()
def langgraph_config() -> dict[str, Any]:
    """A minimal LangGraph RunnableConfig with github_token in configurable."""
    return {
        "configurable": {
            "thread_id": "run-00000000-0000-0000-0000-000000000001",
            "github_token": "ghp_faketoken1234567890",
        }
    }


# ── Supabase fixture ──────────────────────────────────────────────────────────

@pytest.fixture()
def mock_supabase():
    """
    Patch backend.supabase_client.get_supabase() to return a MagicMock.
    Also patches asyncio.to_thread so Supabase lambda calls return MagicMock.
    """
    supabase_mock = MagicMock()
    # Make table().insert/update/select chains return objects with .execute()
    table_mock = MagicMock()
    table_mock.insert.return_value = table_mock
    table_mock.update.return_value = table_mock
    table_mock.select.return_value = table_mock
    table_mock.upsert.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.is_.return_value = table_mock
    table_mock.order.return_value = table_mock
    table_mock.limit.return_value = table_mock
    execute_result = MagicMock()
    execute_result.data = []
    table_mock.execute.return_value = execute_result
    supabase_mock.table.return_value = table_mock

    rpc_mock = MagicMock()
    rpc_execute_result = MagicMock()
    rpc_execute_result.data = []
    rpc_mock.execute.return_value = rpc_execute_result
    supabase_mock.rpc.return_value = rpc_mock

    with (
        patch("backend.supabase_client.get_supabase", return_value=supabase_mock),
        patch("asyncio.to_thread", new=AsyncMock(return_value=execute_result)),
    ):
        yield supabase_mock


# ── GitHub client fixture ─────────────────────────────────────────────────────

@pytest.fixture()
def mock_github_repo():
    """A pre-configured mock GitHub Repository object."""
    repo = MagicMock()
    repo.default_branch = "main"
    repo.full_name = "test-org/test-repo"

    # get_git_tree
    tree = MagicMock()
    tree_element = MagicMock()
    tree_element.path = "backend/main.py"
    tree_element.type = "blob"
    tree.tree = [tree_element]
    repo.get_git_tree.return_value = tree

    # get_branch
    branch = MagicMock()
    branch.commit.sha = "abc123def456"
    repo.get_branch.return_value = branch

    # get_contents
    file_content = MagicMock()
    file_content.size = 500
    file_content.content = "ZnJvbSBmYXN0YXBpIGltcG9ydCBGYXN0QVBJ"  # base64
    file_content.decoded_content = b"from fastapi import FastAPI\napp = FastAPI()\n"
    repo.get_contents.return_value = file_content

    # get_issue
    issue = MagicMock()
    issue.title = "Add rate limiting"
    issue.body = "Please add rate limiting to prevent abuse"
    issue.number = 42
    repo.get_issue.return_value = issue

    return repo


@pytest.fixture()
def mock_github_client(mock_github_repo):
    """Patch backend.github_client functions with safe mocks."""
    with (
        patch("backend.github_client.get_github_client", return_value=MagicMock()),
        patch("backend.github_client.get_repo", return_value=mock_github_repo),
    ):
        yield mock_github_repo


# ── LLM fixture ───────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_llm():
    """
    Patch backend.llm.get_llm() to return a mock with a preset ainvoke.
    The default response content is a valid subtask JSON array.
    """
    llm_mock = MagicMock()
    response_mock = MagicMock()
    response_mock.content = (
        '[{"id":"st-1","title":"Add rate limiting","description":"Implement rate limiting",'
        '"dependencies":[],"likely_files":["backend/main.py"],"complexity":"medium"}]'
    )
    llm_mock.ainvoke = AsyncMock(return_value=response_mock)

    with patch("backend.llm.get_llm", return_value=llm_mock):
        yield llm_mock
