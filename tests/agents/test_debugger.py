"""tests/agents/test_debugger.py — Tests for backend/agents/debugger.py"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def mock_debug_supabase():
    with (
        patch("backend.agents.debugger.update_run_status", new=AsyncMock()),
        patch("backend.agents.debugger.save_agent_output", new=AsyncMock()),
    ):
        yield


@pytest.fixture()
def mock_debug_llm():
    import json
    llm_mock = MagicMock()
    response_mock = MagicMock()
    response_mock.content = json.dumps({
        "failing_test": "test_foo",
        "root_cause": "NullPointerException on line 42",
        "proposed_fix": "Add a None check before the attribute access",
        "confidence": 0.85,
        "target_files": ["backend/main.py"],
    })
    llm_mock.ainvoke = AsyncMock(return_value=response_mock)
    with patch("backend.agents.debugger.get_llm", return_value=llm_mock):
        yield llm_mock


class TestFindRelevantFiles:
    def test_extracts_paths_from_python_traceback(self):
        """_find_relevant_files extracts file paths from Python tracebacks."""
        from backend.agents.debugger import _find_relevant_files

        traceback = '  File "backend/main.py", line 42, in create_app\n    raise ValueError("Oops")'
        file_map = {}
        file_contents = {"backend/main.py": "from fastapi import FastAPI"}

        result = _find_relevant_files(traceback, file_map, file_contents)
        assert "backend/main.py" in result

    def test_falls_back_to_all_file_map_files_when_no_match(self):
        """_find_relevant_files falls back to all file_map entries when traceback yields nothing."""
        from backend.agents.debugger import _find_relevant_files

        file_map = {
            "st-1": [{"path": "backend/main.py", "relevance_score": 0.9, "source": "pgvector"}]
        }
        file_contents = {"backend/main.py": "content"}

        result = _find_relevant_files("No paths here", file_map, file_contents)
        assert "backend/main.py" in result


class TestDebuggerNode:
    @pytest.mark.asyncio
    async def test_returns_debug_report_with_correct_structure(
        self, mock_debug_supabase, mock_debug_llm
    ):
        """debugger_node returns debug_report with fixes and summary."""
        from backend.agents.debugger import debugger_node

        state = {
            "run_id": "run-001",
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
            "file_contents": {},
            "implementation_plan": [],
        }

        result = await debugger_node(state)
        assert "debug_report" in result
        assert "fixes" in result["debug_report"]
        assert "summary" in result["debug_report"]
        assert result["current_agent"] == "debugger"

    @pytest.mark.asyncio
    async def test_empty_failed_list_returns_empty_report_no_llm_call(
        self, mock_debug_supabase, mock_debug_llm
    ):
        """When failed list is empty, debugger returns empty fixes without calling LLM."""
        from backend.agents.debugger import debugger_node

        state = {
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
        }

        result = await debugger_node(state)
        mock_debug_llm.ainvoke.assert_not_called()
        assert result["debug_report"]["fixes"] == []

    @pytest.mark.asyncio
    async def test_suite_did_not_run_explains_exit_code_in_summary(
        self, mock_debug_supabase, mock_debug_llm
    ):
        """When exit_code != 0 and no failures, summary explains the suite never ran."""
        from backend.agents.debugger import debugger_node

        state = {
            "run_id": "run-001",
            "test_results": {
                "framework": "unknown",
                "passed": [],
                "failed": [],
                "passed_count": 0,
                "failed_count": 0,
                "exit_code": 1,
                "stdout": "",
                "stderr": "Sandboxes require an App when created outside of a Modal container.",
            },
            "file_map": {},
            "file_contents": {},
            "implementation_plan": [],
        }

        result = await debugger_node(state)
        mock_debug_llm.ainvoke.assert_not_called()
        summary = result["debug_report"]["summary"]
        assert "did not execute" in summary.lower()
        assert "exit code 1" in summary
        assert "Sandboxes require an App" in summary

    @pytest.mark.asyncio
    async def test_llm_parse_failure_appends_fallback_fix(self, mock_debug_supabase):
        """Bad LLM JSON response produces a fallback fix with confidence=0.0."""
        bad_llm = MagicMock()
        response_mock = MagicMock()
        response_mock.content = "not json"
        bad_llm.ainvoke = AsyncMock(return_value=response_mock)

        with patch("backend.agents.debugger.get_llm", return_value=bad_llm):
            from backend.agents.debugger import debugger_node

            state = {
                "run_id": "run-001",
                "test_results": {
                    "framework": "pytest",
                    "passed": [],
                    "failed": [{"name": "test_foo", "traceback": "Error", "message": "Failed"}],
                    "passed_count": 0, "failed_count": 1,
                    "exit_code": 1, "stdout": "", "stderr": "",
                },
                "file_map": {},
                "file_contents": {},
                "implementation_plan": [],
            }

            result = await debugger_node(state)

        assert len(result["debug_report"]["fixes"]) == 1
        assert result["debug_report"]["fixes"][0]["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_confidence_values_clamped_to_valid_range(
        self, mock_debug_supabase
    ):
        """confidence values must be 0.0–1.0."""
        import json
        llm_mock = MagicMock()
        response_mock = MagicMock()
        response_mock.content = json.dumps({
            "failing_test": "test_x",
            "root_cause": "Bug",
            "proposed_fix": "Fix it",
            "confidence": 0.75,
            "target_files": [],
        })
        llm_mock.ainvoke = AsyncMock(return_value=response_mock)

        with patch("backend.agents.debugger.get_llm", return_value=llm_mock):
            from backend.agents.debugger import debugger_node

            state = {
                "run_id": "run-001",
                "test_results": {
                    "framework": "pytest", "passed": [],
                    "failed": [{"name": "test_x", "traceback": "Err", "message": "Fail"}],
                    "passed_count": 0, "failed_count": 1,
                    "exit_code": 1, "stdout": "", "stderr": "",
                },
                "file_map": {}, "file_contents": {}, "implementation_plan": [],
            }

            result = await debugger_node(state)

        for fix in result["debug_report"]["fixes"]:
            assert 0.0 <= fix["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_persists_full_debug_report_in_agent_output(
        self, mock_debug_llm
    ):
        """Complete-phase payload must include fixes[], not a summary count."""
        saved: list[tuple[str, dict]] = []

        async def capture_save(run_id, agent, payload, phase):
            saved.append((phase, payload))

        with (
            patch("backend.agents.debugger.save_agent_output", side_effect=capture_save),
            patch("backend.agents.debugger.update_run_status", new=AsyncMock()),
        ):
            from backend.agents.debugger import debugger_node

            state = {
                "run_id": "run-001",
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
                "file_contents": {},
                "implementation_plan": [],
            }
            await debugger_node(state)

        complete_payloads = [p for phase, p in saved if phase == "complete"]
        assert complete_payloads
        report = complete_payloads[-1]["debug_report"]
        assert "fixes" in report
        assert isinstance(report["fixes"], list)
        assert len(report["fixes"]) == 1
        assert "summary" in report


class TestSelectFailures:
    def test_caps_and_deduplicates(self):
        from backend.agents.debugger import _select_failures_to_analyse

        failed = [
            {"name": f"test_{i}", "traceback": "err", "message": "fail"}
            for i in range(20)
        ]
        failed.extend(
            [{"name": "test_0", "traceback": "err", "message": "fail"}] * 5
        )
        selected = _select_failures_to_analyse(failed, limit=5)
        assert len(selected) == 5
        names = [f["name"] for f in selected]
        assert names == ["test_0", "test_1", "test_2", "test_3", "test_4"]


class TestDebuggerCaps:
    @pytest.mark.asyncio
    async def test_does_not_call_llm_once_per_failure_on_large_suites(
        self, mock_debug_supabase, mock_debug_llm
    ):
        """Hundreds of failures must not produce hundreds of LLM calls."""
        from backend.agents.debugger import debugger_node

        failed = [
            {"name": f"test_{i}", "traceback": f"AssertionError {i}", "message": "Failed"}
            for i in range(456)
        ]
        state = {
            "run_id": "run-001",
            "test_results": {
                "framework": "pytest",
                "passed": [],
                "failed": failed,
                "passed_count": 0,
                "failed_count": 456,
                "exit_code": 1,
                "stdout": "",
                "stderr": "",
            },
            "file_map": {},
            "file_contents": {},
            "implementation_plan": [],
        }

        result = await debugger_node(state)

        assert mock_debug_llm.ainvoke.await_count == 5
        assert len(result["debug_report"]["fixes"]) == 5
        assert "456" in result["debug_report"]["summary"]
        assert "5 of 456" in result["debug_report"]["summary"]
