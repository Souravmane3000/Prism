"""Minimal timeline label contract for Phase 4 events."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMELINE = ROOT / "frontend" / "lib" / "timeline.ts"


def test_timeline_has_attempt_and_debug_and_rollback_labels():
    src = TIMELINE.read_text(encoding="utf-8")
    assert "presentEvent" in src
    assert "verification.tests.completed" in src
    assert "all_tests_passed" in src
    assert "Verification attempt" in src
    assert "Debugging failure" in src
    assert "Rolling back" in src
    assert 'tone: "error"' in src or 'tone = "error"' in src
