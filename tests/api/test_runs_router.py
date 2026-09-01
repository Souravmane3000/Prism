"""tests/api/test_runs_router.py — Tests for backend/routers/runs.py via FastAPI TestClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


VALID_START_BODY = {
    "repo_url": "https://github.com/test-org/test-repo",
    "issue_text": "Add rate limiting to the public API",
    "github_token": "ghp_faketoken1234567890",
}


@pytest.fixture()
def test_client():
    """Create a TestClient with all external dependencies mocked."""
    with (
        patch("backend.supabase_client.get_supabase", return_value=MagicMock(
            table=MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    limit=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[]))
                    ))
                ))
            ))
        )),
        patch("backend.graph.get_compiled_graph", new=AsyncMock(return_value=MagicMock())),
        patch("httpx.AsyncClient", return_value=MagicMock(
            __aenter__=AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=MagicMock(status_code=200))
            )),
            __aexit__=AsyncMock(return_value=None),
        )),
    ):
        from backend.main import app
        with (
            patch("backend.main.ping_postgres", new=AsyncMock()),
            patch("backend.routers.runs.get_compiled_graph", new=AsyncMock(return_value=MagicMock())),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            yield client


@pytest.fixture()
def mock_db_for_start():
    """Patch the DB functions called by start_run."""
    with (
        patch(
            "backend.routers.runs.create_run",
            new=AsyncMock(return_value="run-00000000-0000-0000-0000-000000000001"),
        ) as create_run_mock,
        patch("backend.routers.runs._schedule_pipeline", new=MagicMock()) as schedule_mock,
    ):
        yield {"create_run": create_run_mock, "schedule": schedule_mock}


@pytest.fixture()
def mock_db_for_status():
    """Patch the DB functions called by get_run_status."""
    run_data = {
        "id": "run-001",
        "status": "running",
        "current_agent": "planner",
        "error": None,
        "all_tests_passed": None,
        "updated_at": "2026-08-12T12:00:00",
    }
    with patch("backend.routers.runs.get_run", new=AsyncMock(return_value=run_data)):
        yield run_data


class TestStartRun:
    def test_valid_request_returns_201_with_run_id(self, test_client, mock_db_for_start):
        """POST /api/runs/start with valid body returns 201 with run_id."""
        response = test_client.post("/api/runs/start", json=VALID_START_BODY)
        assert response.status_code == 201
        data = response.json()
        assert "run_id" in data
        assert data["status"] == "running"

    def test_missing_issue_url_and_text_returns_422(self, test_client, mock_db_for_start):
        """POST /api/runs/start without issue_url or issue_text returns 422."""
        body = {
            "repo_url": "https://github.com/test-org/test-repo",
            "github_token": "ghp_faketoken1234567890",
        }
        response = test_client.post("/api/runs/start", json=body)
        assert response.status_code == 422

    def test_non_github_repo_url_returns_422(self, test_client, mock_db_for_start):
        """POST /api/runs/start with non-GitHub repo_url returns 422."""
        body = {
            "repo_url": "https://gitlab.com/test-org/test-repo",
            "issue_text": "Some issue",
            "github_token": "ghp_faketoken1234567890",
        }
        response = test_client.post("/api/runs/start", json=body)
        assert response.status_code == 422

    def test_github_token_not_echoed_in_response(self, test_client, mock_db_for_start):
        """github_token must NOT appear in the response body."""
        response = test_client.post("/api/runs/start", json=VALID_START_BODY)
        response_text = response.text
        assert "ghp_faketoken1234567890" not in response_text

    def test_response_shape_matches_start_run_response(self, test_client, mock_db_for_start):
        """Response has run_id, status, current_agent."""
        response = test_client.post("/api/runs/start", json=VALID_START_BODY)
        assert response.status_code == 201
        data = response.json()
        assert "run_id" in data
        assert "status" in data
        assert "current_agent" in data

    def test_success_schedules_pipeline_without_echoing_pat(
        self, test_client, mock_db_for_start
    ):
        """201 queues the graph off-request and never returns the GitHub PAT."""
        response = test_client.post("/api/runs/start", json=VALID_START_BODY)
        assert response.status_code == 201
        assert "ghp_faketoken1234567890" not in response.text
        schedule = mock_db_for_start["schedule"]
        schedule.assert_called_once()
        args = schedule.call_args.args
        assert args[0] == "run-00000000-0000-0000-0000-000000000001"
        assert args[1] == VALID_START_BODY["github_token"]

    def test_create_run_failure_returns_db_error_envelope(self, test_client):
        """Frontend parses detail.error.code/message when init fails."""
        with (
            patch(
                "backend.routers.runs.create_run",
                new=AsyncMock(side_effect=RuntimeError("pooler down")),
            ),
            patch("backend.routers.runs._schedule_pipeline", new=MagicMock()) as schedule,
        ):
            response = test_client.post("/api/runs/start", json=VALID_START_BODY)
        assert response.status_code == 500
        err = response.json()["detail"]["error"]
        assert err["code"] == "db_error"
        assert err["message"] == "Failed to initialise run"
        assert err["details"]["exception_type"] == "RuntimeError"
        assert "pooler down" not in response.text
        schedule.assert_not_called()


class TestGetRunStatus:
    def test_known_run_returns_200_with_correct_fields(
        self, test_client, mock_db_for_status
    ):
        """GET /api/runs/{id}/status for known run returns 200 with status fields."""
        response = test_client.get("/api/runs/run-001/status")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-001"
        assert data["status"] == "running"
        assert "current_agent" in data

    def test_unknown_run_returns_404_error_envelope(self, test_client):
        """GET /api/runs/{id}/status for unknown run returns 404 error envelope."""
        with patch("backend.routers.runs.get_run", new=AsyncMock(return_value=None)):
            response = test_client.get("/api/runs/nonexistent-id/status")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data


class TestApproveRun:
    def test_approve_when_not_awaiting_returns_409(self, test_client):
        """POST /api/runs/{id}/approve when status is not awaiting_approval returns 409."""
        run_data = {
            "id": "run-001",
            "status": "running",
            "current_agent": "planner",
        }
        with patch("backend.routers.runs.get_run", new=AsyncMock(return_value=run_data)):
            response = test_client.post(
                "/api/runs/run-001/approve",
                json={
                    "checkpoint": "hitl_1",
                    "action": "approve",
                    "github_token": "ghp_faketoken1234567890",
                },
            )
        assert response.status_code == 409

    def test_stop_action_returns_200_with_cancelled_status(self, test_client):
        """POST /api/runs/{id}/approve with action=stop returns 200 with status=cancelled."""
        run_data = {
            "id": "run-001",
            "status": "awaiting_approval",
            "current_agent": "hitl_1",
        }
        with (
            patch("backend.routers.runs.get_run", new=AsyncMock(return_value=run_data)),
            patch("backend.routers.runs.resolve_checkpoint", new=AsyncMock()),
            patch("backend.routers.runs.save_agent_output", new=AsyncMock()),
            patch("backend.routers.runs.update_run_status", new=AsyncMock()),
        ):
            response = test_client.post(
                "/api/runs/run-001/approve",
                json={
                    "checkpoint": "hitl_1",
                    "action": "stop",
                    "github_token": "ghp_faketoken1234567890",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

    def test_github_token_absent_from_approve_response(self, test_client):
        """github_token field is absent from the approve response body."""
        run_data = {
            "id": "run-001",
            "status": "awaiting_approval",
            "current_agent": "hitl_1",
        }
        mock_graph = MagicMock()
        mock_graph.aupdate_state = AsyncMock()

        with (
            patch("backend.routers.runs.get_run", new=AsyncMock(return_value=run_data)),
            patch("backend.routers.runs.resolve_checkpoint", new=AsyncMock()),
            patch("backend.routers.runs.save_agent_output", new=AsyncMock()),
            patch("backend.routers.runs.update_run_status", new=AsyncMock()),
            patch("backend.routers.runs.get_compiled_graph", new=AsyncMock(return_value=mock_graph)),
            patch("backend.routers.runs._schedule_pipeline", new=MagicMock()),
        ):
            response = test_client.post(
                "/api/runs/run-001/approve",
                json={
                    "checkpoint": "hitl_1",
                    "action": "approve",
                    "github_token": "ghp_supersecrettoken",
                },
            )
        assert "ghp_supersecrettoken" not in response.text


class TestCreatePR:
    def test_create_pr_when_not_completed_returns_409(self, test_client):
        """POST /api/runs/{id}/create-pr when run is not completed returns 409."""
        run_data = {
            "id": "run-001",
            "status": "running",
            "repo_url": "https://github.com/test-org/test-repo",
        }
        with (
            patch("backend.routers.runs.get_run", new=AsyncMock(return_value=run_data)),
            patch("backend.routers.runs.get_run_outputs", new=AsyncMock(return_value=[])),
        ):
            response = test_client.post(
                "/api/runs/run-001/create-pr",
                json={"github_token": "ghp_faketoken1234567890"},
            )
        assert response.status_code == 409

    def test_error_responses_match_error_response_envelope(self, test_client):
        """Error responses include the ErrorResponse envelope shape."""
        with patch("backend.routers.runs.get_run", new=AsyncMock(return_value=None)):
            response = test_client.get("/api/runs/nonexistent/status")
        assert response.status_code == 404
        data = response.json()
        # FastAPI returns detail field for HTTPException
        assert "detail" in data


class TestGetRunOutput:
    def test_prefers_full_pr_draft_from_checkpoint(self, test_client):
        """GET /output overlays LangGraph checkpoint so truncated agent payloads still render."""
        run_data = {
            "id": "run-001",
            "status": "completed",
            "current_agent": "pr_summarizer",
            "repo_url": "https://github.com/tiangolo/fastapi",
            "issue_url": None,
            "issue_text": "Add validation",
            "all_tests_passed": True,
            "error": None,
            "pr_url": None,
        }
        truncated_outputs = [
            {
                "phase": "complete",
                "agent": "pr_summarizer",
                "payload": {"pr_draft": {"title": "Add validation", "checklist_items": 2}},
            }
        ]
        full_draft = {
            "title": "Add validation",
            "body": "Full PR body",
            "what_changed": "Added validators",
            "why": "Issue requested it",
            "testing_notes": "pytest",
            "limitations": "None",
            "review_checklist": ["Check min_length"],
        }
        full_results = {
            "framework": "pytest",
            "passed": ["test_ok"],
            "failed": [],
            "passed_count": 1,
            "failed_count": 0,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        }
        mock_graph = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.values = {
            "pr_draft": full_draft,
            "test_results": full_results,
            "all_tests_passed": True,
            "debug_report": None,
        }
        mock_graph.aget_state = AsyncMock(return_value=mock_snapshot)

        with (
            patch("backend.routers.runs.get_run", new=AsyncMock(return_value=run_data)),
            patch(
                "backend.routers.runs.get_run_outputs",
                new=AsyncMock(return_value=truncated_outputs),
            ),
            patch(
                "backend.graph.get_compiled_graph",
                new=AsyncMock(return_value=mock_graph),
            ),
        ):
            response = test_client.get("/api/runs/run-001/output")

        assert response.status_code == 200
        data = response.json()
        assert data["pr_draft"]["body"] == "Full PR body"
        assert data["pr_draft"]["review_checklist"] == ["Check min_length"]
        assert data["test_results"]["failed"] == []
        assert data["all_tests_passed"] is True

    def test_empty_checkpoint_does_not_wipe_agent_output_fields(self, test_client):
        """Blank LangGraph values must not clobber planner/file_map already in agent_outputs."""
        run_data = {
            "id": "run-001",
            "status": "completed",
            "current_agent": "pr_summarizer",
            "repo_url": "https://github.com/tiangolo/fastapi",
            "issue_url": None,
            "issue_text": "Add validation",
            "all_tests_passed": True,
            "error": None,
            "pr_url": None,
        }
        outputs = [
            {
                "phase": "complete",
                "agent": "planner",
                "payload": {
                    "subtasks": [
                        {
                            "id": "st-1",
                            "title": "Add field",
                            "description": "desc",
                            "dependencies": [],
                            "likely_files": ["app.py"],
                            "complexity": "low",
                        }
                    ]
                },
            },
            {
                "phase": "complete",
                "agent": "code_navigator",
                "payload": {
                    "file_map": {
                        "st-1": [
                            {
                                "path": "app.py",
                                "relevance_score": 0.9,
                                "source": "github",
                            }
                        ]
                    }
                },
            },
            {
                "phase": "complete",
                "agent": "pr_summarizer",
                "payload": {
                    "pr_draft": {
                        "title": "Add field",
                        "body": "Adds the field",
                        "what_changed": "app.py",
                        "why": "issue",
                        "testing_notes": "n/a",
                        "limitations": "none",
                        "review_checklist": ["Check types"],
                    }
                },
            },
        ]
        mock_graph = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.values = {
            "subtasks": [],
            "file_map": {},
            "implementation_plan": [],
            "pr_draft": None,
            "test_results": None,
            "debug_report": None,
        }
        mock_graph.aget_state = AsyncMock(return_value=mock_snapshot)

        with (
            patch("backend.routers.runs.get_run", new=AsyncMock(return_value=run_data)),
            patch(
                "backend.routers.runs.get_run_outputs",
                new=AsyncMock(return_value=outputs),
            ),
            patch(
                "backend.graph.get_compiled_graph",
                new=AsyncMock(return_value=mock_graph),
            ),
        ):
            response = test_client.get("/api/runs/run-001/output")

        assert response.status_code == 200
        data = response.json()
        assert data["subtasks"][0]["id"] == "st-1"
        assert "app.py" in [e["path"] for e in data["file_map"]["st-1"]]
        assert data["pr_draft"]["title"] == "Add field"


class TestPickRicher:
    def test_blank_checkpoint_does_not_clobber_outputs(self):
        from backend.routers.runs import _pick_richer

        subtasks = [{"id": "st-1", "title": "x"}]
        assert _pick_richer("subtasks", subtasks, []) == subtasks
        assert _pick_richer("file_map", {"st-1": [{"path": "a.py"}]}, {}) == {
            "st-1": [{"path": "a.py"}]
        }
