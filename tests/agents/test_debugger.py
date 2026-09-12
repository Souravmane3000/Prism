"""tests/agents/test_debugger.py — Debugger Phase 4 contract."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.patch.spec import ModificationSpec


@pytest.fixture()
def mock_debug_supabase():
    with (
        patch("backend.agents.debugger.update_run_status", new=AsyncMock()),
        patch("backend.agents.debugger.emit_event", new=AsyncMock()),
        patch("backend.agents.debugger.emit_error_event", new=AsyncMock()),
    ):
        yield


@pytest.fixture()
def mock_debug_structured():
    from backend.agents.debugger import DebugFixModel

    model = DebugFixModel(
        failing_test="test_foo",
        root_cause="NullPointerException on line 42",
        proposed_fix="Add a None check before the attribute access",
        confidence=0.85,
        target_files=["backend/main.py"],
    )
    with patch("backend.agents.debugger.invoke_structured", new=AsyncMock(return_value=model)):
        yield


class TestFindRelevantFiles:
    def test_extracts_paths_from_python_traceback(self):
        from backend.agents.debugger import _find_relevant_files

        traceback = '  File "backend/main.py", line 42, in create_app\n    raise ValueError("Oops")'
        result = _find_relevant_files(traceback, {}, {"backend/main.py": "from fastapi import FastAPI"})
        assert "backend/main.py" in result


class TestDebuggerNode:
    @pytest.mark.asyncio
    async def test_safe_fix_sets_can_generate_fix(
        self, mock_debug_supabase, mock_debug_structured
    ):
        from backend.agents.debugger import debugger_node

        result = await debugger_node(
            {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
                "test_results": {
                    "framework": "pytest",
                    "passed": [],
                    "failed": [{"name": "test_foo", "traceback": "AssertionError", "message": "Failed"}],
                    "passed_count": 0,
                    "failed_count": 1,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "",
                },
                "file_map": {},
                "file_contents": {"backend/main.py": "x = 1\n"},
                "implementation_plan": [],
            },
            {"configurable": {"github_token": ""}},
        )
        assert result["debug_report"]["can_generate_fix"] is True
        assert result["session_status"] == "RUNNING"
        assert "verification_attempts" not in result

    @pytest.mark.asyncio
    async def test_empty_failed_list_does_not_fabricate_patch(
        self, mock_debug_supabase
    ):
        from backend.agents.debugger import debugger_node

        with patch("backend.agents.debugger.invoke_structured", new=AsyncMock()) as inv:
            result = await debugger_node(
                {
                    "run_id": "run-001",
                    "test_results": {
                        "framework": "pytest",
                        "passed": ["test_foo"],
                        "failed": [],
                        "passed_count": 1,
                        "failed_count": 0,
                        "exit_code": 0,
                        "stdout": "1 passed",
                        "stderr": "",
                    },
                    "file_map": {},
                    "file_contents": {},
                    "implementation_plan": [],
                },
                {"configurable": {"github_token": ""}},
            )
        inv.assert_not_called()
        assert result["debug_report"]["can_generate_fix"] is False
        assert result["session_status"] == "PATCH_ERROR"

    @pytest.mark.asyncio
    async def test_low_confidence_is_not_a_safe_fix(self, mock_debug_supabase):
        from backend.agents.debugger import DebugFixModel, debugger_node

        model = DebugFixModel(
            failing_test="test_foo",
            root_cause="unclear",
            proposed_fix="maybe something",
            confidence=0.2,
            target_files=["backend/main.py"],
        )
        with patch("backend.agents.debugger.invoke_structured", new=AsyncMock(return_value=model)):
            result = await debugger_node(
                {
                    "run_id": "run-001",
                    "repo_url": "https://github.com/x/y",
                    "test_results": {
                        "framework": "pytest",
                        "passed": [],
                        "failed": [{"name": "test_foo", "traceback": "err", "message": "fail"}],
                        "passed_count": 0,
                        "failed_count": 1,
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "",
                    },
                    "file_map": {},
                    "file_contents": {},
                    "implementation_plan": [],
                },
                {"configurable": {"github_token": ""}},
            )
        assert result["debug_report"]["can_generate_fix"] is False
        assert result["session_status"] == "PATCH_ERROR"


class TestSelectFailures:
    def test_caps_and_deduplicates(self):
        from backend.agents.debugger import _select_failures_to_analyse

        failed = [{"name": f"test_{i}", "traceback": "err", "message": "fail"} for i in range(20)]
        selected = _select_failures_to_analyse(failed, limit=5)
        assert len(selected) == 5
