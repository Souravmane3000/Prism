"""tests/agents/test_planner.py — Tests for backend/agents/planner.py"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


VALID_SUBTASKS_JSON = json.dumps([
    {
        "id": "st-1",
        "title": "Add rate limiting",
        "description": "Implement request rate limiting",
        "dependencies": [],
        "likely_files": ["backend/main.py"],
        "complexity": "medium",
    }
])


def _make_config(token: str = "ghp_faketoken") -> dict:
    return {"configurable": {"thread_id": "run-001", "github_token": token}}


@pytest.fixture()
def mock_supabase_calls():
    """Patch all supabase_client async functions used by planner."""
    with (
        patch("backend.agents.planner.update_run_status", new=AsyncMock()),
        patch("backend.agents.planner.save_agent_output", new=AsyncMock()),
    ):
        yield


@pytest.fixture()
def mock_github_for_planner():
    """Patch github_client functions and LLM for planner tests.

    Patches backend.agents.planner.get_llm directly (not backend.llm)
    because the planner imports the function at module level and Python mock
    must replace the name where it is *used*, not where it is *defined*.
    """
    llm_mock = MagicMock()
    response_mock = MagicMock()
    response_mock.content = VALID_SUBTASKS_JSON
    llm_mock.ainvoke = AsyncMock(return_value=response_mock)

    repo_mock = MagicMock()
    branch_mock = MagicMock()
    branch_mock.commit.sha = "abc123"
    repo_mock.get_branch.return_value = branch_mock
    repo_mock.default_branch = "main"
    tree_mock = MagicMock()
    tree_elem = MagicMock()
    tree_elem.path = "backend/main.py"
    tree_elem.type = "blob"
    tree_mock.tree = [tree_elem]
    repo_mock.get_git_tree.return_value = tree_mock
    repo_mock.get_contents.return_value = MagicMock(
        size=100, encoding="base64",
        content="IyBSRUFETUUK",  # "# README\n" in base64
        decoded_content=b"# README\n",
    )
    issue_mock = MagicMock()
    issue_mock.title = "Fix rate limiting"
    issue_mock.body = "Add rate limiting to the API"
    repo_mock.get_issue.return_value = issue_mock

    with (
        patch("backend.agents.planner.get_github_client", return_value=MagicMock()),
        patch("backend.agents.planner.get_repo", return_value=repo_mock),
        patch("backend.agents.planner.get_file_tree", return_value=["backend/main.py"]),
        patch("backend.agents.planner.get_file_content", return_value="# README\n"),
        patch("backend.agents.planner.get_issue", return_value={
            "title": "Fix rate limiting",
            "body": "Add rate limiting to the API",
        }),
        patch("backend.agents.planner.get_llm", return_value=llm_mock),
    ):
        yield repo_mock


class TestPlannerNode:
    @pytest.mark.asyncio
    async def test_returns_subtasks_and_required_keys(
        self, mock_supabase_calls, mock_github_for_planner, mock_llm
    ):
        """planner_node returns subtasks, repo_tree, issue_text, current_agent."""
        from backend.agents.planner import planner_node

        state = {
            "run_id": "run-001",
            "repo_url": "https://github.com/owner/repo",
            "issue_url": "https://github.com/owner/repo/issues/42",
            "issue_text": None,
        }

        result = await planner_node(state, _make_config())

        assert "subtasks" in result
        assert "repo_tree" in result
        assert result["current_agent"] == "planner"
        assert isinstance(result["messages"], list)

    @pytest.mark.asyncio
    async def test_uses_issue_text_directly_when_no_url(
        self, mock_supabase_calls, mock_github_for_planner, mock_llm
    ):
        """When issue_url is absent, planner uses issue_text directly."""
        from backend.agents.planner import planner_node

        state = {
            "run_id": "run-001",
            "repo_url": "https://github.com/owner/repo",
            "issue_url": None,
            "issue_text": "Add rate limiting to prevent abuse",
        }

        result = await planner_node(state, _make_config())
        assert result.get("error") is None

    @pytest.mark.asyncio
    async def test_returns_error_when_no_issue_content(
        self, mock_supabase_calls, mock_github_for_planner, mock_llm
    ):
        """When both issue_url and issue_text are empty, returns error without raising."""
        with patch("backend.agents.planner.get_issue", side_effect=Exception("Not found")):
            from backend.agents.planner import planner_node

            state = {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
                "issue_url": None,
                "issue_text": None,
            }

            result = await planner_node(state, _make_config())
            assert "error" in result
            assert result["current_agent"] == "planner"

    @pytest.mark.asyncio
    async def test_malformed_llm_json_returns_error(
        self, mock_supabase_calls, mock_github_for_planner
    ):
        """Non-array LLM JSON response returns error field without raising."""
        bad_llm = MagicMock()
        response_mock = MagicMock()
        response_mock.content = '{"not": "an array"}'
        bad_llm.ainvoke = AsyncMock(return_value=response_mock)

        with patch("backend.agents.planner.get_llm", return_value=bad_llm):
            from backend.agents.planner import planner_node

            state = {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
                "issue_url": None,
                "issue_text": "Some issue text here",
            }

            result = await planner_node(state, _make_config())
            assert "error" in result

    @pytest.mark.asyncio
    async def test_github_token_not_in_save_agent_output_payload(
        self, mock_github_for_planner, mock_llm
    ):
        """github_token must never appear in save_agent_output payloads."""
        saved_payloads = []

        async def capture_save(run_id, agent, payload, phase):
            saved_payloads.append(payload)

        with (
            patch("backend.agents.planner.update_run_status", new=AsyncMock()),
            patch("backend.agents.planner.save_agent_output", side_effect=capture_save),
        ):
            from backend.agents.planner import planner_node

            state = {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
                "issue_url": None,
                "issue_text": "Add rate limiting",
            }

            await planner_node(state, _make_config("ghp_supersecret1234567890"))

        for payload in saved_payloads:
            payload_str = str(payload)
            assert "ghp_supersecret1234567890" not in payload_str, (
                "github_token leaked into save_agent_output payload!"
            )

    @pytest.mark.asyncio
    async def test_supabase_called_with_start_and_complete_phases(
        self, mock_github_for_planner, mock_llm
    ):
        """save_agent_output is called with 'start' then 'complete' phases."""
        phases_seen = []

        async def capture_save(run_id, agent, payload, phase):
            phases_seen.append(phase)

        with (
            patch("backend.agents.planner.update_run_status", new=AsyncMock()),
            patch("backend.agents.planner.save_agent_output", side_effect=capture_save),
        ):
            from backend.agents.planner import planner_node

            state = {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
                "issue_url": None,
                "issue_text": "Add rate limiting",
            }

            await planner_node(state, _make_config())

        assert "start" in phases_seen
        assert "complete" in phases_seen
        assert phases_seen.index("start") < phases_seen.index("complete")
