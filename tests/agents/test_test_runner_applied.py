"""Real pytest on an applied local workspace."""

from backend.agents.test_runner import _run_pytest_local
from backend.patch.apply import apply_patch
from backend.patch.generate import generate_structured_patch
from backend.patch.spec import FileChange, ModificationSpec, Replacement
from backend.workspace import LocalGitWorkspace


def test_pytest_on_applied_workspace_can_fail_then_pass():
    files = {
        "mod.py": "def add(a, b):\n    return a - b\n",
        "tests/test_mod.py": "from mod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        "pytest.ini": "[pytest]\n",
    }
    with LocalGitWorkspace.init_fixture(files) as ws:
        stdout, stderr, code, _cmd, _ms = _run_pytest_local(ws)
        assert code != 0

        spec = ModificationSpec(
            files=[
                FileChange(
                    path="mod.py",
                    operation="modify",
                    replacements=[Replacement(old="return a - b", new="return a + b")],
                )
            ]
        )
        ws.reset_to_baseline()
        patch = generate_structured_patch(spec, ws, baseline_sha=ws.baseline_sha)
        apply_patch(
            patch=patch,
            patch_status="APPROVED",
            baseline_sha=ws.baseline_sha,
            workspace=ws,
        )
        stdout2, stderr2, code2, _, _ = _run_pytest_local(ws)
        assert code2 == 0
        assert ws.head_sha() == ws.baseline_sha
