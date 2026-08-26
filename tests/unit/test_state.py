"""tests/unit/test_state.py — Tests for backend/state.py"""

import operator
from typing import get_type_hints

import pytest

from backend.state import (
    DebugFix,
    DebugReport,
    FileMapEntry,
    ImplementationPlanItem,
    ImplementationStep,
    PRDraft,
    PrismState,
    Subtask,
    TestFailure,
    TestResults,
)


class TestSubtask:
    def test_can_instantiate_with_required_keys(self):
        st = Subtask(
            id="st-1",
            title="Add rate limiting",
            description="Implement rate limiting",
            dependencies=[],
            likely_files=["backend/main.py"],
            complexity="medium",
        )
        assert st["id"] == "st-1"
        assert st["complexity"] == "medium"

    def test_complexity_accepts_low_medium_high(self):
        for val in ("low", "medium", "high"):
            st = Subtask(
                id="st-1", title="T", description="D",
                dependencies=[], likely_files=[], complexity=val
            )
            assert st["complexity"] == val


class TestFileMapEntry:
    def test_can_instantiate(self):
        entry = FileMapEntry(path="foo.py", relevance_score=0.85, source="pgvector")
        assert entry["path"] == "foo.py"
        assert entry["source"] == "pgvector"


class TestImplementationPlanItem:
    def test_can_instantiate_with_steps(self):
        step = ImplementationStep(
            order=1,
            file="backend/main.py",
            function_or_symbol="create_app",
            change_description="Add middleware",
            rationale="Required for rate limiting",
            tradeoffs=["Adds latency"],
        )
        item = ImplementationPlanItem(subtask_id="st-1", steps=[step])
        assert item["subtask_id"] == "st-1"
        assert len(item["steps"]) == 1


class TestTestResults:
    def test_can_instantiate(self):
        results = TestResults(
            framework="pytest",
            passed=["test_a"],
            failed=[],
            passed_count=1,
            failed_count=0,
            exit_code=0,
            stdout="1 passed",
            stderr="",
        )
        assert results["framework"] == "pytest"
        assert results["exit_code"] == 0


class TestDebugReport:
    def test_can_instantiate_with_empty_fixes(self):
        report = DebugReport(fixes=[], summary="No failures")
        assert report["summary"] == "No failures"

    def test_can_instantiate_with_fixes(self):
        fix = DebugFix(
            failing_test="test_foo",
            root_cause="NullPointerException in line 42",
            proposed_fix="Add null check before access",
            confidence=0.85,
            target_files=["backend/main.py"],
        )
        report = DebugReport(fixes=[fix], summary="1 fix proposed")
        assert len(report["fixes"]) == 1
        assert report["fixes"][0]["confidence"] == 0.85


class TestPRDraft:
    def test_can_instantiate(self):
        draft = PRDraft(
            title="Add rate limiting to public API",
            body="This PR adds...",
            what_changed="Added SlowAPI middleware",
            why="Prevents API abuse",
            testing_notes="Run tests with pytest",
            limitations="Redis required in prod",
            review_checklist=["Verify rate limits are correct"],
        )
        assert draft["title"].startswith("Add")


class TestPrismState:
    def test_github_token_not_in_prism_state(self):
        """github_token must not be a field in PrismState (security requirement)."""
        hints = get_type_hints(PrismState)
        assert "github_token" not in hints, (
            "github_token MUST NOT be in PrismState — it must travel via "
            "config['configurable']['github_token'] only."
        )

    def test_messages_uses_operator_add_reducer(self):
        """PrismState.messages uses operator.add as the LangGraph reducer annotation."""
        import typing
        hints = get_type_hints(PrismState, include_extras=True)
        messages_annotation = hints.get("messages")
        assert messages_annotation is not None
        # Annotated[list[str], operator.add] — check the metadata
        metadata = getattr(messages_annotation, "__metadata__", ())
        assert operator.add in metadata, "messages field must be annotated with operator.add"

    def test_run_id_field_exists(self):
        hints = get_type_hints(PrismState)
        assert "run_id" in hints

    def test_all_required_pipeline_fields_present(self):
        """All pipeline stage output fields are present in PrismState."""
        hints = get_type_hints(PrismState)
        required = [
            "repo_url", "issue_url", "issue_text", "run_id",
            "repo_tree", "subtasks", "planner_approved",
            "file_map", "file_contents",
            "implementation_plan", "impl_approved",
            "test_results", "all_tests_passed",
            "debug_report", "pr_draft",
            "current_agent", "error", "messages",
        ]
        for field in required:
            assert field in hints, f"PrismState missing required field: {field}"
