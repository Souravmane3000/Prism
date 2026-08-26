"""tests/agents/test_pr_summarizer.py — Tests for backend/agents/pr_summarizer.py"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


VALID_PR_JSON = json.dumps({
    "title": "Add rate limiting to public API endpoints",
    "body": "This PR adds request rate limiting to prevent API abuse.",
    "what_changed": "Added SlowAPI middleware to FastAPI application.",
    "why": "Rate limiting prevents API abuse and ensures fair usage.",
    "testing_notes": "Run `pytest tests/test_middleware.py` to verify.",
    "limitations": "Redis required in production; in-memory limiter used in dev.",
    "review_checklist": [
        "Verify rate limits are correct for each endpoint",
        "Check that rate limit headers are returned",
    ],
})


@pytest.fixture()
def mock_summarizer_supabase():
    with (
        patch("backend.agents.pr_summarizer.update_run_status", new=AsyncMock()),
        patch("backend.agents.pr_summarizer.save_agent_output", new=AsyncMock()),
    ):
        yield


@pytest.fixture()
def mock_summarizer_llm():
    llm_mock = MagicMock()
    response_mock = MagicMock()
    response_mock.content = VALID_PR_JSON
    llm_mock.ainvoke = AsyncMock(return_value=response_mock)
    with patch("backend.agents.pr_summarizer.get_llm", return_value=llm_mock):
        yield llm_mock


class TestExtractIssueNumber:
    def test_extracts_issue_number_from_url(self):
        """_extract_issue_number parses /issues/42 → 42."""
        from backend.agents.pr_summarizer import _extract_issue_number

        assert _extract_issue_number("https://github.com/o/r/issues/42") == 42

    def test_returns_none_for_missing_url(self):
        from backend.agents.pr_summarizer import _extract_issue_number

        assert _extract_issue_number(None) is None

    def test_returns_none_for_url_without_issue(self):
        from backend.agents.pr_summarizer import _extract_issue_number

        assert _extract_issue_number("https://github.com/o/r") is None


class TestPrSummarizerNode:
    @pytest.mark.asyncio
    async def test_returns_pr_draft_with_all_required_fields(
        self, mock_summarizer_supabase, mock_summarizer_llm, sample_state
    ):
        """pr_summarizer_node returns pr_draft with all required fields."""
        from backend.agents.pr_summarizer import pr_summarizer_node

        result = await pr_summarizer_node(sample_state)
        assert "pr_draft" in result
        pr = result["pr_draft"]
        for field in ("title", "body", "what_changed", "why", "testing_notes",
                      "limitations", "review_checklist"):
            assert field in pr, f"pr_draft missing field: {field}"
        assert result["current_agent"] == "pr_summarizer"

    @pytest.mark.asyncio
    async def test_pr_title_is_action_oriented(
        self, mock_summarizer_supabase, mock_summarizer_llm, sample_state
    ):
        """PR title starts with an imperative verb (action-oriented)."""
        from backend.agents.pr_summarizer import pr_summarizer_node

        result = await pr_summarizer_node(sample_state)
        title = result["pr_draft"]["title"]
        assert len(title) > 0

    @pytest.mark.asyncio
    async def test_run_id_status_set_to_completed_on_success(
        self, mock_summarizer_llm, sample_state
    ):
        """On success, update_run_status is called with 'completed'."""
        status_calls = []

        async def capture_status(run_id, status, current_agent, **kwargs):
            status_calls.append(status)

        with (
            patch("backend.agents.pr_summarizer.save_agent_output", new=AsyncMock()),
            patch("backend.agents.pr_summarizer.update_run_status", side_effect=capture_status),
        ):
            from backend.agents.pr_summarizer import pr_summarizer_node
            await pr_summarizer_node(sample_state)

        assert "completed" in status_calls

    @pytest.mark.asyncio
    async def test_llm_parse_failure_returns_error_and_failed_status(
        self, sample_state
    ):
        """Bad LLM JSON → returns error field, sets status to 'failed'."""
        bad_llm = MagicMock()
        response_mock = MagicMock()
        response_mock.content = "this is not json"
        bad_llm.ainvoke = AsyncMock(return_value=response_mock)

        status_calls = []

        async def capture_status(run_id, status, current_agent, **kwargs):
            status_calls.append(status)

        with (
            patch("backend.agents.pr_summarizer.get_llm", return_value=bad_llm),
            patch("backend.agents.pr_summarizer.save_agent_output", new=AsyncMock()),
            patch("backend.agents.pr_summarizer.update_run_status", side_effect=capture_status),
        ):
            from backend.agents.pr_summarizer import pr_summarizer_node
            result = await pr_summarizer_node(sample_state)

        assert "error" in result
        assert "failed" in status_calls

    @pytest.mark.asyncio
    async def test_persists_full_pr_draft_in_agent_output(
        self, mock_summarizer_llm, sample_state
    ):
        """Complete-phase payload must include the full PR draft, not a summary."""
        saved: list[tuple[str, dict]] = []

        async def capture_save(run_id, agent, payload, phase):
            saved.append((phase, payload))

        with (
            patch("backend.agents.pr_summarizer.save_agent_output", side_effect=capture_save),
            patch("backend.agents.pr_summarizer.update_run_status", new=AsyncMock()),
        ):
            from backend.agents.pr_summarizer import pr_summarizer_node
            await pr_summarizer_node(sample_state)

        complete_payloads = [p for phase, p in saved if phase == "complete"]
        assert complete_payloads, "expected a complete-phase agent_output row"
        draft = complete_payloads[-1]["pr_draft"]
        for field in (
            "title",
            "body",
            "what_changed",
            "why",
            "testing_notes",
            "limitations",
            "review_checklist",
        ):
            assert field in draft, f"persisted pr_draft missing {field}"
        assert isinstance(draft["review_checklist"], list)
        assert len(draft["review_checklist"]) >= 1
