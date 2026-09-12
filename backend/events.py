"""Validated Event Adapter for Prism execution visibility."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Final

from pydantic import BaseModel, Field, JsonValue, field_validator

from backend.supabase_client import append_session_event, save_agent_output

logger = logging.getLogger(__name__)

MAX_EVENT_PAYLOAD_BYTES: Final = 64 * 1024
EVENT_TYPE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")

RUN_STARTED: Final = "run.started"
RUN_COMPLETED: Final = "run.completed"
PATCH_GENERATION_STARTED: Final = "patch.generation.started"
PATCH_GENERATION_COMPLETED: Final = "patch.generation.completed"
PATCH_GENERATION_FAILED: Final = "patch.generation.failed"
PATCH_VALIDATION_STARTED: Final = "patch.validation.started"
PATCH_VALIDATION_COMPLETED: Final = "patch.validation.completed"
PATCH_APPLICATION_STARTED: Final = "patch.application.started"
PATCH_APPLICATION_COMPLETED: Final = "patch.application.completed"
PATCH_APPLICATION_FAILED: Final = "patch.application.failed"
VERIFICATION_STARTED: Final = "verification.tests.started"
VERIFICATION_COMPLETED: Final = "verification.tests.completed"
DEBUGGER_STARTED: Final = "debugger.analysis.started"
DEBUGGER_COMPLETED: Final = "debugger.analysis.completed"
DEBUGGER_FAILED: Final = "debugger.analysis.failed"
ROLLBACK_STARTED: Final = "rollback.started"
ROLLBACK_COMPLETED: Final = "rollback.completed"
ROLLBACK_FAILED: Final = "rollback.failed"
ERROR_OCCURRED: Final = "error.occurred"

_FORBIDDEN_KEYS: Final = frozenset(
    {
        "authorization",
        "github_pat",
        "github_token",
        "langsmith_api_key",
        "openai_api_key",
        "password",
        "pat",
        "secret",
        "service_role_key",
        "supabase_service_key",
        "token",
    }
)
_SECRET_VALUE_PATTERN: Final = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{12,}|sk-[A-Za-z0-9_-]{12,})"
)

_EVENT_AGENT: dict[str, tuple[str, str]] = {
    PATCH_GENERATION_STARTED: ("code_modifier", "start"),
    PATCH_GENERATION_COMPLETED: ("code_modifier", "complete"),
    PATCH_VALIDATION_STARTED: ("validate_patch", "start"),
    PATCH_VALIDATION_COMPLETED: ("validate_patch", "complete"),
    PATCH_APPLICATION_STARTED: ("apply_patch", "start"),
    PATCH_APPLICATION_COMPLETED: ("apply_patch", "complete"),
    VERIFICATION_STARTED: ("test_runner", "start"),
    VERIFICATION_COMPLETED: ("test_runner", "complete"),
    DEBUGGER_STARTED: ("debugger", "start"),
    DEBUGGER_COMPLETED: ("debugger", "complete"),
    DEBUGGER_FAILED: ("debugger", "complete"),
    ROLLBACK_STARTED: ("rollback", "start"),
    ROLLBACK_COMPLETED: ("rollback", "complete"),
    ROLLBACK_FAILED: ("rollback", "complete"),
}


class EventValidationError(ValueError):
    """Raised before persistence when an event violates the public contract."""


class EventPersistenceError(RuntimeError):
    """Raised when a valid event cannot be durably appended."""


def _validate_payload_safety(value: JsonValue, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.strip().lower()
            if normalized in _FORBIDDEN_KEYS or normalized.endswith("_secret"):
                raise ValueError(f"{path}.{key} is not allowed in an event payload")
            _validate_payload_safety(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_payload_safety(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and _SECRET_VALUE_PATTERN.search(value):
        raise ValueError(f"{path} contains a token-like value")


class SessionEventCreate(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=3, max_length=160)
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("run_id must not be blank")
        return stripped

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        if EVENT_TYPE_PATTERN.fullmatch(value) is None:
            raise ValueError("event_type must be a lowercase dotted identifier")
        return value

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        encoded = json.dumps(value, default=str)
        if len(encoded.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("payload exceeds 64 KiB")
        _validate_payload_safety(value)
        return value


def sanitize_log_text(text: str, *, limit: int = 4000) -> str:
    """Bound and redact token-like values from test logs before LLM/events."""
    cleaned = _SECRET_VALUE_PATTERN.sub("[REDACTED]", text or "")
    if len(cleaned) > limit:
        return cleaned[:limit] + "\n[truncated]"
    return cleaned


async def emit_event(run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    body = payload or {}
    try:
        event = SessionEventCreate(run_id=run_id, event_type=event_type, payload=body)
    except Exception as exc:
        raise EventValidationError(str(exc)) from exc
    try:
        await append_session_event(event.run_id, event.event_type, dict(event.payload))
    except Exception as exc:
        logger.warning("session_events persist failed: %s", exc)
    mapped = _EVENT_AGENT.get(event_type)
    if mapped:
        agent, phase = mapped
        origin = str(body.get("patch_origin") or "")
        if event_type.startswith("patch.generation") and origin == "debugger":
            agent = "fix_patch"
        try:
            await save_agent_output(run_id, agent, dict(event.payload), phase)
        except Exception as exc:
            logger.warning("agent_outputs mirror failed: %s", exc)


async def emit_error_event(run_id: str, stage: str, exc: BaseException) -> None:
    await emit_event(
        run_id,
        ERROR_OCCURRED,
        {
            "stage": stage,
            "error_type": type(exc).__name__,
            "summary": str(exc)[:500],
        },
    )
