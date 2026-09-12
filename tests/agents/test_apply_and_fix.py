"""tests/agents/test_apply_and_fix.py"""

from unittest.mock import AsyncMock, patch as mock_patch

import pytest

from backend.patch.generate import generate_structured_patch
from backend.patch.spec import FileChange, ModificationSpec, Replacement
from backend.workspace import LocalGitWorkspace


@pytest.mark.asyncio
async def test_apply_patch_node_does_not_write_baseline_or_attempts():
    with LocalGitWorkspace.init_fixture({"a.py": "x = 1\n"}) as ws:
        structured = generate_structured_patch(
            ModificationSpec(
                files=[
                    FileChange(
                        path="a.py",
                        operation="modify",
                        replacements=[Replacement(old="x = 1", new="x = 2")],
                    )
                ]
            ),
            ws,
            baseline_sha=ws.baseline_sha,
        )
        ws.reset_to_baseline()
        with (
            mock_patch("backend.agents.apply_patch.update_run_status", new=AsyncMock()),
            mock_patch("backend.agents.apply_patch.emit_event", new=AsyncMock()),
            mock_patch("backend.agents.apply_patch.emit_error_event", new=AsyncMock()),
            mock_patch("backend.agents.apply_patch.open_mutation_workspace", return_value=ws),
        ):
            from backend.agents.apply_patch import apply_patch_node

            result = await apply_patch_node(
                {
                    "run_id": "run-1",
                    "repo_url": "https://github.com/x/y",
                    "baseline_sha": ws.baseline_sha,
                    "current_patch": structured,
                    "patch_status": "VALIDATED",
                },
                {"configurable": {"github_token": "t"}},
            )
            # Node closes the workspace in finally; keep assertions on the dict.
            assert result["patch_status"] == "APPLIED"
            assert "baseline_sha" not in result
            assert "verification_attempts" not in result


@pytest.mark.asyncio
async def test_apply_patch_failure_does_not_claim_applied():
    with (
        mock_patch("backend.agents.apply_patch.update_run_status", new=AsyncMock()),
        mock_patch("backend.agents.apply_patch.emit_event", new=AsyncMock()),
        mock_patch("backend.agents.apply_patch.emit_error_event", new=AsyncMock()),
    ):
        from backend.agents.apply_patch import apply_patch_node

        result = await apply_patch_node(
            {
                "run_id": "run-1",
                "repo_url": "https://github.com/x/y",
                "baseline_sha": None,
                "current_patch": None,
                "patch_status": "VALIDATED",
            },
            {"configurable": {"github_token": "t"}},
        )
    assert result["session_status"] == "PATCH_ERROR"
    assert result["patch_status"] != "APPLIED"
    assert "verification_attempts" not in result


@pytest.mark.asyncio
async def test_fix_patch_without_safe_diagnosis_is_patch_error():
    with (
        mock_patch("backend.agents.fix_patch.update_run_status", new=AsyncMock()),
        mock_patch("backend.agents.fix_patch.emit_event", new=AsyncMock()),
        mock_patch("backend.agents.fix_patch.emit_error_event", new=AsyncMock()),
    ):
        from backend.agents.fix_patch import fix_patch_node

        result = await fix_patch_node(
            {
                "run_id": "run-1",
                "repo_url": "https://github.com/x/y",
                "debug_report": {"can_generate_fix": False, "fixes": [], "summary": "nope"},
                "verification_attempts": 1,
            },
            {"configurable": {"github_token": "t"}},
        )
    assert result["session_status"] == "PATCH_ERROR"
    assert result.get("patch_status") != "GENERATED"
    assert "verification_attempts" not in result


@pytest.mark.asyncio
async def test_max_verification_failure_requires_rollback():
    from backend.agents.max_verification_failure import max_verification_failure_node

    with (
        mock_patch("backend.agents.max_verification_failure.update_run_status", new=AsyncMock()),
        mock_patch("backend.agents.max_verification_failure.emit_event", new=AsyncMock()),
    ):
        bad = await max_verification_failure_node({"run_id": "r", "patch_status": "APPLIED"})
        assert bad["session_status"] == "PATCH_ERROR"
        ok = await max_verification_failure_node(
            {"run_id": "r", "patch_status": "ROLLED_BACK", "verification_attempts": 3}
        )
        assert ok["session_status"] == "FAILED_MAX_VERIFICATION"
