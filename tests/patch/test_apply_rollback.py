"""Patch apply, rollback, and cumulative fix generation."""

from backend.patch.apply import apply_patch, materialize_patch
from backend.patch.generate import generate_cumulative_structured_patch, generate_structured_patch
from backend.patch.rollback import rollback_to_baseline
from backend.patch.spec import FileChange, ModificationSpec, Replacement
from backend.patch.validate import validate_structured_patch
from backend.workspace import LocalGitWorkspace


def _spec_replace(path: str, old: str, new: str) -> ModificationSpec:
    return ModificationSpec(
        files=[FileChange(path=path, operation="modify", replacements=[Replacement(old=old, new=new)])],
        summary="test",
    )


class TestGenerateApplyRollback:
    def test_apply_then_rollback_restores_baseline(self):
        with LocalGitWorkspace.init_fixture(
            {
                "mod.py": "VALUE = 1\n",
                "tests/test_mod.py": "from mod import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
            }
        ) as ws:
            spec = _spec_replace("mod.py", "VALUE = 1", "VALUE = 2")
            patch = generate_structured_patch(spec, ws, baseline_sha=ws.baseline_sha)
            result = validate_structured_patch(patch, expected_baseline=ws.baseline_sha)
            assert result.ok
            applied = apply_patch(
                patch=patch,
                patch_status="APPROVED",
                baseline_sha=ws.baseline_sha,
                workspace=ws,
            )
            assert applied.patch_status == "APPLIED"
            assert ws.head_sha() == ws.baseline_sha
            assert "VALUE = 2" in ws.read_file("mod.py")
            rolled = rollback_to_baseline(ws, baseline_sha=ws.baseline_sha)
            assert rolled.working_tree_clean
            assert ws.read_file("mod.py") == "VALUE = 1\n"
            assert ws.head_sha() == ws.baseline_sha

    def test_unsafe_git_path_is_rejected(self):
        from backend.patch.paths import UnsafePathError, normalize_repo_path
        import pytest

        with pytest.raises(UnsafePathError):
            normalize_repo_path(".git/config")
        with pytest.raises(UnsafePathError):
            normalize_repo_path("../secret")

    def test_cumulative_fix_preserves_original_and_prior_fix(self):
        with LocalGitWorkspace.init_fixture({"mod.py": "A = 0\nB = 0\n"}) as ws:
            original = generate_structured_patch(
                _spec_replace("mod.py", "A = 0", "A = 1"),
                ws,
                baseline_sha=ws.baseline_sha,
            )
            ws.reset_to_baseline()
            materialize_patch(ws, original)
            assert "A = 1" in ws.read_file("mod.py")
            cumulative = generate_cumulative_structured_patch(
                _spec_replace("mod.py", "B = 0", "B = 2"),
                ws,
                baseline_sha=ws.baseline_sha,
            )
            assert cumulative["baseline_sha"] == original["baseline_sha"]
            assert cumulative["patch_id"] != original["patch_id"]
            ws.reset_to_baseline()
            materialize_patch(ws, cumulative)
            text = ws.read_file("mod.py")
            assert "A = 1" in text
            assert "B = 2" in text
            diff = ws.diff_against(ws.baseline_sha)
            assert "A = 1" in diff
            assert "B = 2" in diff

    def test_rollback_removes_added_debug_file(self):
        from backend.patch.spec import FileChange

        with LocalGitWorkspace.init_fixture({"mod.py": "VALUE = 1\n"}) as ws:
            spec = ModificationSpec(
                files=[
                    FileChange(path="debug_extra.py", operation="add", new_content="EXTRA = 1\n"),
                    FileChange(
                        path="mod.py",
                        operation="modify",
                        replacements=[Replacement(old="VALUE = 1", new="VALUE = 2")],
                    ),
                ],
                summary="add and modify",
            )
            patch = generate_structured_patch(spec, ws, baseline_sha=ws.baseline_sha)
            ws.reset_to_baseline()
            apply_patch(
                patch=patch,
                patch_status="APPROVED",
                baseline_sha=ws.baseline_sha,
                workspace=ws,
            )
            assert ws.file_exists("debug_extra.py")
            rollback_to_baseline(ws, baseline_sha=ws.baseline_sha)
            assert not ws.file_exists("debug_extra.py")
            assert ws.read_file("mod.py") == "VALUE = 1\n"
            assert ws.head_sha() == ws.baseline_sha
            assert ws.working_tree_clean()
