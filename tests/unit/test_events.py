"""Event Adapter payload safety."""

import pytest

from backend.events import EventValidationError, SessionEventCreate, sanitize_log_text


def test_rejects_token_keys():
    with pytest.raises(Exception):
        SessionEventCreate(run_id="run-1", event_type="verification.tests.started", payload={"github_token": "x"})


def test_rejects_token_like_values():
    with pytest.raises(Exception):
        SessionEventCreate(
            run_id="run-1",
            event_type="verification.tests.completed",
            payload={"summary": "ghp_abcdefghijklmnopqrstuvwxyz"},
        )


def test_sanitize_redacts_and_truncates():
    text = "secret ghp_abcdefghijklmnopqrstuvwxyz " + ("x" * 5000)
    cleaned = sanitize_log_text(text, limit=100)
    assert "ghp_" not in cleaned
    assert "[truncated]" in cleaned or len(cleaned) <= 120
