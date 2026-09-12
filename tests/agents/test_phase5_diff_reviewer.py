"""Phase 5 — Diff Reviewer + HITL #3 scenario matrix."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.state import (
    MAX_HITL_REVISIONS,
    ReviewerFinding,
    v2_state_defaults,
)


def _reviewed_state(**overrides: Any) -> dict[str, Any]:
    state = {
        **v2_state_defaults(),
        "run_id": "run-phase5",
        "repo_url": "https://github.com/example/repo",
        "issue_text": "Fix auth bug",
        "implementation_plan": [{"subtask_id": "s1", "steps": []}],
        "current_patch": {
            "schema_version": "1",
            "patch_id": "p1",
            "baseline_sha": "abc123",
            "unified_diff": "diff --git a/a.py b/a.py\n+print(1)\n",
            "modified_files": [{"path": "a.py", "operation": "modify", "old_path": None}],
            "checksum_sha256": "deadbeef",
        },
        "patch_origin": "code_modifier",
        "patch_status": "REVIEWED",
        "review_decision": "APPROVE",
        "review_summary": "Looks good",
        "reviewed_files": ["a.py"],
        "review_risk_level": "low",
        "reviewer_findings": [],
        "hitl_revision_count": 0,
        "session_status": "AWAITING_HITL_3",
        "current_agent": "hitl_3",
    }
    state.update(overrides)
    return state


class TestPhase5Routing:
    def test_validated_code_modifier_goes_to_diff_reviewer(self):
        from backend.graph import route_after_validate_patch

        assert (
            route_after_validate_patch(
                {"patch_status": "VALIDATED", "patch_origin": "code_modifier"}
            )
            == "diff_reviewer"
        )

    def test_validated_debugger_bypasses_review(self):
        from backend.graph import route_after_validate_patch

        assert (
            route_after_validate_patch(
                {"patch_status": "VALIDATED", "patch_origin": "debugger"}
            )
            == "apply_patch"
        )

    def test_reviewed_goes_to_hitl_3(self):
        from backend.graph import route_after_diff_reviewer

        assert (
            route_after_diff_reviewer({"patch_status": "REVIEWED", "session_status": "AWAITING_HITL_3"})
            == "hitl_3"
        )

    def test_reviewer_failure_ends(self):
        from backend.graph import route_after_diff_reviewer

        assert route_after_diff_reviewer({"session_status": "LLM_ERROR"}) == "end"
        assert route_after_diff_reviewer({"session_status": "CONTEXT_OVERFLOW"}) == "end"

    def test_hitl3_approve_to_apply(self):
        from backend.graph import route_after_hitl_3

        assert (
            route_after_hitl_3({"patch_status": "APPROVED", "session_status": "RUNNING"})
            == "apply_patch"
        )

    def test_hitl3_revise_to_code_modifier(self):
        from backend.graph import route_after_hitl_3

        assert (
            route_after_hitl_3(
                {
                    "patch_status": "REJECTED",
                    "session_status": "RUNNING",
                    "hitl_revision_count": 1,
                }
            )
            == "code_modifier"
        )

    def test_hitl3_max_revisions_terminal(self):
        from backend.graph import route_after_hitl_3

        assert (
            route_after_hitl_3(
                {
                    "patch_status": "REJECTED",
                    "session_status": "FAILED_MAX_REVISIONS",
                    "hitl_revision_count": 2,
                }
            )
            == "max_revisions_failure"
        )

    def test_hitl3_stop_ends_without_rollback(self):
        from backend.graph import route_after_hitl_3

        assert (
            route_after_hitl_3(
                {"session_status": "USER_TERMINATED", "patch_status": "REVIEWED"}
            )
            == "end"
        )


class TestPhase5GraphTopology:
    def test_registers_diff_reviewer_and_hitl3(self):
        from backend.graph import build_graph

        builder = build_graph()
        if hasattr(builder, "_nodes"):
            registered = set(builder._nodes.keys()) - {"__start__", "__end__"}
            assert "diff_reviewer" in registered
            assert "hitl_3" in registered
            assert "max_revisions_failure" in registered

    def test_interrupt_before_includes_hitl3(self):
        import inspect

        from backend import graph as graph_module

        source = inspect.getsource(graph_module.get_compiled_graph)
        compact = source.replace(" ", "")
        assert 'interrupt_before=["hitl_1","hitl_2","hitl_3"]' in compact


class TestApplyOriginGate:
    def test_validated_without_debugger_origin_blocked(self):
        from backend.patch.apply import ApplyError, check_apply_preconditions

        patch = {
            "schema_version": "1",
            "patch_id": "p1",
            "baseline_sha": "abc",
            "unified_diff": "diff\n",
            "modified_files": [],
            "checksum_sha256": "x",
        }
        with pytest.raises(ApplyError) as exc:
            check_apply_preconditions(
                patch=patch,
                patch_status="VALIDATED",
                baseline_sha="abc",
                patch_origin="code_modifier",
            )
        assert exc.value.reason == "invalid_lifecycle"

    def test_approved_allowed(self):
        from backend.patch.apply import check_apply_preconditions

        patch = {
            "schema_version": "1",
            "patch_id": "p1",
            "baseline_sha": "abc",
            "unified_diff": "diff\n",
            "modified_files": [],
            "checksum_sha256": "x",
        }
        # May still fail baseline checks if workspace missing — only lifecycle gate here
        try:
            check_apply_preconditions(
                patch=patch,
                patch_status="APPROVED",
                baseline_sha="abc",
                patch_origin="code_modifier",
            )
        except Exception as exc:
            # baseline/workspace errors are ok; lifecycle must not be the reason
            assert getattr(exc, "reason", None) != "invalid_lifecycle"

    def test_reviewed_blocked(self):
        from backend.patch.apply import ApplyError, check_apply_preconditions

        with pytest.raises(ApplyError):
            check_apply_preconditions(
                patch={
                    "schema_version": "1",
                    "patch_id": "p1",
                    "baseline_sha": "abc",
                    "unified_diff": "diff\n",
                    "modified_files": [],
                    "checksum_sha256": "x",
                },
                patch_status="REVIEWED",
                baseline_sha="abc",
                patch_origin="code_modifier",
            )


class TestApproveHitl3Contract:
    def test_revise_requires_feedback(self):
        from backend.routers.runs import ApproveRunRequest

        with pytest.raises(ValidationError):
            ApproveRunRequest(
                checkpoint="hitl_3",
                action="revise",
                human_feedback="   ",
                github_token="ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            )

    def test_revise_accepts_nonempty_feedback(self):
        from backend.routers.runs import ApproveRunRequest

        body = ApproveRunRequest(
            checkpoint="hitl_3",
            action="revise",
            human_feedback=" Please fix auth ",
            github_token="ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        )
        assert body.human_feedback == "Please fix auth"

    def test_hitl3_rejects_edit_action(self):
        from backend.routers.runs import ApproveRunRequest

        with pytest.raises(ValidationError):
            ApproveRunRequest(
                checkpoint="hitl_3",
                action="edit",
                github_token="ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            )


class TestDiffReviewerNode:
    @pytest.mark.asyncio
    async def test_reviewer_approve_sets_reviewed(self):
        from backend.agents.diff_reviewer import DiffReviewResult, diff_reviewer_node

        state = _reviewed_state(patch_status="VALIDATED", session_status="RUNNING")
        result = DiffReviewResult(
            decision="APPROVE",
            summary="Patch addresses the plan",
            risk_level="low",
            reviewed_files=["a.py"],
            findings=[],
        )
        with (
            patch("backend.agents.diff_reviewer.update_run_status", new=AsyncMock()),
            patch("backend.agents.diff_reviewer.emit_event", new=AsyncMock()),
            patch(
                "backend.agents.diff_reviewer.assemble_code_modifier_context",
                return_value=MagicMock(text="ctx"),
            ),
            patch(
                "backend.agents.diff_reviewer.invoke_structured",
                new=AsyncMock(return_value=result),
            ),
        ):
            out = await diff_reviewer_node(state, {})
        assert out["patch_status"] == "REVIEWED"
        assert out["review_decision"] == "APPROVE"
        assert out["session_status"] == "AWAITING_HITL_3"

    @pytest.mark.asyncio
    async def test_reviewer_context_overflow_no_approve(self):
        from backend.agents.diff_reviewer import diff_reviewer_node
        from backend.llm import ContextOverflowError

        state = _reviewed_state(patch_status="VALIDATED", session_status="RUNNING")
        with (
            patch("backend.agents.diff_reviewer.update_run_status", new=AsyncMock()),
            patch("backend.agents.diff_reviewer.emit_event", new=AsyncMock()),
            patch("backend.agents.diff_reviewer.emit_error_event", new=AsyncMock()),
            patch(
                "backend.agents.diff_reviewer.assemble_code_modifier_context",
                side_effect=ContextOverflowError("too large"),
            ),
        ):
            out = await diff_reviewer_node(state, {})
        assert out["session_status"] == "CONTEXT_OVERFLOW"
        assert out.get("patch_status") != "APPROVED"
        assert out.get("review_decision") is None

    @pytest.mark.asyncio
    async def test_reviewer_malformed_output_exhausts_without_approve(self):
        from backend.agents.diff_reviewer import diff_reviewer_node
        from backend.llm import DeterministicLLMError

        state = _reviewed_state(patch_status="VALIDATED", session_status="RUNNING")
        with (
            patch("backend.agents.diff_reviewer.update_run_status", new=AsyncMock()),
            patch("backend.agents.diff_reviewer.emit_event", new=AsyncMock()),
            patch("backend.agents.diff_reviewer.emit_error_event", new=AsyncMock()),
            patch(
                "backend.agents.diff_reviewer.assemble_code_modifier_context",
                return_value=MagicMock(text="ctx"),
            ),
            patch(
                "backend.agents.diff_reviewer.invoke_structured",
                new=AsyncMock(side_effect=DeterministicLLMError("bad json")),
            ),
        ):
            out = await diff_reviewer_node(state, {})
        assert out["session_status"] == "LLM_ERROR"
        assert "review_decision" not in out or out.get("review_decision") is None


class TestRevisionSemantics:
    def test_max_hitl_revisions_is_two(self):
        assert MAX_HITL_REVISIONS == 2

    def test_counters_are_independent_in_defaults(self):
        defaults = v2_state_defaults()
        assert defaults["hitl_revision_count"] == 0
        assert defaults["verification_attempts"] == 0
        assert "review_decision" in defaults
        assert defaults["reviewed_files"] == []

    def test_reviewer_finding_shape(self):
        finding: ReviewerFinding = {
            "severity": "blocking",
            "category": "correctness",
            "file_path": "a.py",
            "line_start": 10,
            "line_end": None,
            "description": "Missing null check",
            "why_it_matters": "Can crash",
            "recommended_action": "Add guard",
        }
        assert finding["severity"] == "blocking"


class TestCodeModifierRevisionContext:
    def test_header_includes_feedback_and_findings(self):
        from backend.agents.code_modifier import _build_modifier_header

        header = _build_modifier_header(
            {
                "issue_text": "bug",
                "implementation_plan": [],
                "hitl_revision_count": 1,
                "human_patch_feedback": "Fix the auth path",
                "rejected_diff": "diff --git a/x",
                "reviewer_findings": [{"description": "bad"}],
            }
        )
        assert "Revision context" in header
        assert "Fix the auth path" in header
        assert "rejected_diff" in header


class TestEventsPhase5:
    def test_review_and_hitl_event_constants_exist(self):
        from backend import events

        assert events.REVIEW_DIFF_STARTED == "review.diff.started"
        assert events.HITL_REVIEW_PRESENTED == "hitl.review.presented"
        assert events.HITL_REVIEW_CHANGES_REQUESTED == "hitl.review.changes_requested"
        assert events.REVISION_LIMIT_REACHED == "revision.limit_reached"

    @pytest.mark.asyncio
    async def test_emit_rejects_pat_in_review_payload(self):
        from backend.events import EventValidationError, emit_event

        with pytest.raises(EventValidationError):
            await emit_event(
                "run-1",
                "review.diff.completed",
                {"github_token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"},
            )


class TestMaxRevisionsFailureNode:
    @pytest.mark.asyncio
    async def test_sets_failed_max_revisions(self):
        from backend.agents.max_revisions_failure import max_revisions_failure_node

        with (
            patch("backend.agents.max_revisions_failure.emit_event", new=AsyncMock()),
            patch(
                "backend.agents.max_revisions_failure.update_run_status",
                new=AsyncMock(),
            ),
        ):
            out = await max_revisions_failure_node(
                {"run_id": "r1", "hitl_revision_count": 2, "patch_status": "REJECTED"}
            )
        assert out["session_status"] == "FAILED_MAX_REVISIONS"
