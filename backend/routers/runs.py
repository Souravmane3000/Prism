"""
backend/routers/runs.py — All /api/runs/* route handlers.

Implements the five endpoints specified in API.md:
  POST   /api/runs/start
  GET    /api/runs/{id}/status
  GET    /api/runs/{id}/output
  POST   /api/runs/{id}/approve
  POST   /api/runs/{id}/create-pr

Security:
- GitHub PAT is accepted in request body for start/approve/create-pr.
  It is passed in-flight via LangGraph config["configurable"]["github_token"].
  It is NEVER written to state (and therefore NEVER serialised by the Postgres
  checkpointer), NEVER logged, NEVER returned in any response body.
- Only the last 4 characters are stored in runs.github_token_hint for audit.
"""

import asyncio
import inspect
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from github.GithubException import GithubException

import backend.config  # noqa: F401 — LangSmith env before LangGraph
from backend.config import configure_langsmith_tracing, flush_langsmith_traces
from backend.github_client import (
    commit_file,
    create_branch,
    create_pull_request,
    format_github_write_error,
    get_github_client,
    get_repo,
)
from backend.graph import get_compiled_graph
from backend.state import PrismState
from backend.supabase_client import (
    create_checkpoint,
    create_run,
    get_run,
    get_run_outputs,
    is_transient_http_error,
    resolve_checkpoint,
    save_agent_output,
    update_run_status,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_GRAPH_TRANSIENT_ATTEMPTS = 3
_HITL_NODES = frozenset({"hitl_1", "hitl_2"})


def _graph_run_config(run_id: str, github_token: str) -> dict[str, Any]:
    """LangGraph config: thread, PAT, and LangSmith run identity."""
    short_id = run_id[:8]
    return {
        "run_name": f"prism:{short_id}",
        "tags": ["prism", f"run:{short_id}"],
        "metadata": {"prism_run_id": run_id},
        "configurable": {
            "thread_id": run_id,
            "github_token": github_token,
        },
    }


def _paused_hitl_node(snapshot: Any) -> Optional[str]:
    """Return hitl_1/hitl_2 if the graph is stopped at that interrupt_before node."""
    nxt = getattr(snapshot, "next", None)
    if not nxt:
        return None
    try:
        name = nxt[0]
    except (TypeError, IndexError, KeyError):
        return None
    if name in _HITL_NODES:
        return str(name)
    return None


async def _astream_with_retry(
    graph: Any,
    input_state: Any,
    config: dict[str, Any],
    run_id: str,
) -> None:
    """
    Stream the graph, retrying HTTP/2 drops.

    After the first failure, subsequent attempts pass None so LangGraph
    resumes from the Postgres checkpoint instead of restarting the thread.

    interrupt_before=["hitl_1", "hitl_2"] can leave astream blocked on the next
    iteration waiting for a node that will never run. After each yielded chunk
    we read aget_state and return as soon as the next node is a HITL gate.
    """
    from langsmith.run_helpers import tracing_context

    project = configure_langsmith_tracing()
    pending: Any = input_state
    for attempt in range(1, _GRAPH_TRANSIENT_ATTEMPTS + 1):
        try:
            with tracing_context(
                enabled=True,
                project_name=project,
                tags=["prism", f"run:{run_id[:8]}"],
                metadata={"prism_run_id": run_id},
            ):
                async for _chunk in graph.astream(pending, config, stream_mode="values"):
                    try:
                        snapshot = await graph.aget_state(config)
                    except Exception:
                        logger.warning(
                            "[runs] aget_state during astream failed — run_id=%s",
                            run_id,
                            exc_info=True,
                        )
                        continue
                    paused = _paused_hitl_node(snapshot)
                    if paused:
                        logger.info(
                            "[runs] astream reached HITL boundary — run_id=%s next=%s",
                            run_id,
                            paused,
                        )
                        return
            return
        except Exception as exc:
            try:
                snapshot = await graph.aget_state(config)
                paused = _paused_hitl_node(snapshot)
                if paused:
                    logger.info(
                        "[runs] astream interrupted at HITL — run_id=%s next=%s err=%s",
                        run_id,
                        paused,
                        type(exc).__name__,
                    )
                    return
            except Exception:
                pass
            if not is_transient_http_error(exc) or attempt == _GRAPH_TRANSIENT_ATTEMPTS:
                raise
            logger.warning(
                "[runs] Transient HTTP error on graph.astream attempt %s/%s — run_id=%s: %s",
                attempt,
                _GRAPH_TRANSIENT_ATTEMPTS,
                run_id,
                exc,
            )
            await asyncio.sleep(0.8 * attempt)
            pending = None

# ── Pydantic request / response models ─────────────────────────────────────────


class StartRunRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub repository URL")
    issue_url: Optional[str] = Field(None, description="GitHub issue URL")
    issue_text: Optional[str] = Field(None, description="Pasted issue body text")
    github_token: str = Field(..., description="GitHub PAT — in-flight only, never stored")

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, v: str) -> str:
        if "github.com" not in v:
            raise ValueError("repo_url must be a GitHub repository URL")
        return v.rstrip("/")

    @field_validator("github_token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        if not v or len(v) < 4:
            raise ValueError("github_token must be a valid GitHub PAT")
        return v

    @model_validator(mode="after")
    def check_issue_provided(self) -> "StartRunRequest":
        if not self.issue_url and not self.issue_text:
            raise ValueError("Provide at least one of issue_url or issue_text")
        return self


class StartRunResponse(BaseModel):
    run_id: str
    status: str
    current_agent: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    current_agent: str
    error: Optional[str] = None
    all_tests_passed: Optional[bool] = None
    updated_at: Optional[str] = None


class RunOutputResponse(BaseModel):
    run_id: str
    status: str
    current_agent: str
    repo_url: str
    issue_url: Optional[str] = None
    issue_text: Optional[str] = None
    subtasks: list[Any] = Field(default_factory=list)
    planner_approved: Optional[bool] = None
    file_map: dict[str, Any] = Field(default_factory=dict)
    implementation_plan: list[Any] = Field(default_factory=list)
    impl_approved: Optional[bool] = None
    test_results: Optional[Any] = None
    all_tests_passed: Optional[bool] = None
    debug_report: Optional[Any] = None
    pr_draft: Optional[Any] = None
    messages: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    pr_url: Optional[str] = None


class ApproveRunRequest(BaseModel):
    checkpoint: str = Field(..., description="'hitl_1' or 'hitl_2'")
    action: str = Field(..., description="approve | edit | revise | restart | stop")
    subtasks: Optional[list[Any]] = Field(None, description="Edited subtasks for HITL 1")
    implementation_plan: Optional[list[Any]] = Field(
        None, description="Revised plan for HITL 2"
    )
    github_token: str = Field(..., description="GitHub PAT — re-supplied because it is not stored")

    @field_validator("checkpoint")
    @classmethod
    def validate_checkpoint(cls, v: str) -> str:
        if v not in ("hitl_1", "hitl_2"):
            raise ValueError("checkpoint must be 'hitl_1' or 'hitl_2'")
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        allowed = {"approve", "edit", "revise", "restart", "stop"}
        if v not in allowed:
            raise ValueError(f"action must be one of {allowed}")
        return v


class ApproveRunResponse(BaseModel):
    run_id: str
    status: str
    current_agent: str
    message: str


class CreatePRRequest(BaseModel):
    github_token: str = Field(..., description="GitHub PAT — in-flight only")
    head_branch: Optional[str] = Field(None, description="Source branch (default: prism/<run_id>)")
    base_branch: Optional[str] = Field(None, description="Target branch (default: repo default)")
    commit_message: Optional[str] = Field(None)


class CreatePRResponse(BaseModel):
    run_id: str
    pr_url: str
    pr_number: Optional[int] = None
    title: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    run_id: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ── Helpers ────────────────────────────────────────────────────────────────────

def _redact_token(token: str) -> str:
    """Return a redacted representation safe for logging."""
    return f"***{token[-4:]}" if len(token) >= 4 else "***"


def _is_full_pr_draft(val: Any) -> bool:
    return isinstance(val, dict) and "body" in val and isinstance(val.get("review_checklist"), list)


def _is_full_test_results(val: Any) -> bool:
    return isinstance(val, dict) and isinstance(val.get("failed"), list)


def _is_full_debug_report(val: Any) -> bool:
    return isinstance(val, dict) and isinstance(val.get("fixes"), list)


def _is_blank(val: Any) -> bool:
    """True for None / empty collections so they cannot clobber real agent output."""
    if val is None:
        return True
    if val == "" or val == [] or val == {}:
        return True
    return False


def _pick_richer(key: str, from_outputs: Any, from_checkpoint: Any) -> Any:
    """Prefer the complete object when agent_outputs historically stored a summary.

    Empty checkpoint values ([], {}, None) must not wipe planner subtasks,
    file_map, or a PR draft that already landed in agent_outputs.
    """
    if key == "pr_draft":
        if _is_full_pr_draft(from_checkpoint):
            return from_checkpoint
        if _is_full_pr_draft(from_outputs):
            return from_outputs
    elif key == "test_results":
        if _is_full_test_results(from_checkpoint):
            return from_checkpoint
        if _is_full_test_results(from_outputs):
            return from_outputs
    elif key == "debug_report":
        if _is_full_debug_report(from_checkpoint):
            return from_checkpoint
        if _is_full_debug_report(from_outputs):
            return from_outputs
    if _is_blank(from_checkpoint):
        return from_outputs
    if _is_blank(from_outputs):
        return from_checkpoint
    return from_checkpoint


_CHECKPOINT_LOAD_TIMEOUT_SECONDS = 5.0


async def _load_checkpoint_values(run_id: str) -> dict[str, Any]:
    """
    Read the LangGraph checkpointer snapshot for a run.

    Agent output rows are the Realtime/UI denormalised copy. The checkpointer
    holds the full PrismState, which older agent payloads truncated.

    Timed out so GET /output still returns agent_outputs when the checkpointer
    pool is busy (file_contents snapshots can stall aget_state).
    """
    try:
        # Late import so tests that patch backend.graph.get_compiled_graph apply.
        from backend.graph import get_compiled_graph as load_graph

        graph = await load_graph()
        maybe = graph.aget_state({"configurable": {"thread_id": run_id}})
        if inspect.isawaitable(maybe):
            snapshot = await asyncio.wait_for(
                maybe, timeout=_CHECKPOINT_LOAD_TIMEOUT_SECONDS
            )
        else:
            snapshot = maybe
        values = getattr(snapshot, "values", None)
        if isinstance(values, dict):
            return dict(values)
    except TimeoutError:
        logger.warning(
            "[runs] Timed out reading graph checkpoint for %s — using agent_outputs",
            run_id,
        )
    except Exception as exc:
        logger.warning("[runs] Could not read graph checkpoint for %s: %s", run_id, exc)
    return {}


async def _materialize_run_state(
    run_id: str, outputs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Merge complete agent_outputs payloads with the graph checkpoint."""
    state_snapshot: dict[str, Any] = {}
    for row in outputs:
        if row.get("phase") == "complete" and row.get("payload"):
            state_snapshot.update(row["payload"])

    checkpoint = await _load_checkpoint_values(run_id)
    merged = dict(state_snapshot)
    for key in (
        "pr_draft",
        "test_results",
        "debug_report",
        "all_tests_passed",
        "subtasks",
        "file_map",
        "implementation_plan",
        "planner_approved",
        "impl_approved",
        "messages",
    ):
        merged[key] = _pick_richer(key, state_snapshot.get(key), checkpoint.get(key))
    return merged


def _error_response(
    code: str,
    message: str,
    http_status: int,
    run_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail=ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                run_id=run_id,
                details=details or {},
            )
        ).model_dump(),
    )


async def _handle_hitl_pause(
    run_id: str,
    checkpoint_name: str,
    state_values: dict[str, Any],
) -> None:
    """
    Write pause state to DB after graph stops at an interrupt_before boundary.

    Order matters: checkpoint record and agent-output row are written FIRST;
    update_run_status("awaiting_approval") is written LAST so the Realtime
    event that triggers the frontend HITL card only fires once everything is
    already committed.  That eliminates the race where the card appears before
    the checkpoint row exists and the user clicks Approve before the record
    is ready.
    """
    logger.info("[runs] _handle_hitl_pause STARTED — checkpoint=%s run_id=%s", checkpoint_name, run_id)
    
    subtasks: list[Any] = state_values.get("subtasks", [])
    implementation_plan: list[Any] = state_values.get("implementation_plan", [])
    logger.info("[runs] _handle_hitl_pause — subtasks=%d impl_plan=%d", len(subtasks), len(implementation_plan))

    if checkpoint_name == "hitl_1":
        checkpoint_payload: dict[str, Any] = {
            "checkpoint": "hitl_1",
            "run_id": run_id,
            "type": "subtask_approval",
            "subtasks": subtasks,
            "actions_allowed": ["approve", "edit", "restart"],
        }
    else:  # hitl_2
        checkpoint_payload = {
            "checkpoint": "hitl_2",
            "run_id": run_id,
            "type": "implementation_plan_approval",
            "implementation_plan": implementation_plan,
            "actions_allowed": ["approve", "revise", "stop"],
        }

    # Write checkpoint record and agent-output first so the data is ready
    # before the status Realtime event reaches the frontend.
    logger.info("[runs] _handle_hitl_pause — calling create_checkpoint")
    await create_checkpoint(run_id, checkpoint_name, checkpoint_payload)
    logger.info("[runs] _handle_hitl_pause — calling save_agent_output")
    await save_agent_output(run_id, checkpoint_name, {"status": "awaiting_approval"}, "start")
    logger.info("[runs] _handle_hitl_pause — calling update_run_status")
    # Status update is the final write; its Realtime event triggers the HITL card.
    # Explicitly clear error to ensure no stale error persists from previous states.
    await update_run_status(run_id, "awaiting_approval", checkpoint_name, error="")
    logger.info("[runs] _handle_hitl_pause COMPLETED — checkpoint=%s run_id=%s", checkpoint_name, run_id)


async def _reconcile_hitl_pause(run_id: str, run: dict[str, Any]) -> dict[str, Any]:
    """
    If LangGraph is paused at HITL but runs.status was never flipped (astream
    hang, timeout, or missed handler), write the pause records now.

    Called from GET /status and GET /output so a stuck run heals when the UI polls.
    """
    if run.get("status") in ("awaiting_approval", "completed", "cancelled", "failed"):
        return run
    try:
        graph = await get_compiled_graph()
        snapshot = await graph.aget_state({"configurable": {"thread_id": run_id}})
        paused = _paused_hitl_node(snapshot)
        if not paused:
            return run
        values = getattr(snapshot, "values", None)
        state_values = dict(values) if isinstance(values, dict) else {}
        logger.info(
            "[runs] Reconciling missed HITL pause — run_id=%s checkpoint=%s",
            run_id,
            paused,
        )
        await _handle_hitl_pause(run_id, paused, state_values)
        updated = await get_run(run_id)
        return updated or run
    except Exception as exc:
        logger.warning("[runs] HITL reconcile skipped for %s: %s", run_id, exc)
        return run


async def _run_graph_background(
    run_id: str,
    initial_state: PrismState,
    github_token: str,
) -> None:
    """
    Execute the graph from the beginning as a background task.

    Uses interrupt_before (set at compile time in graph.py) for HITL pauses.
    After astream ends, aget_state determines whether the graph is paused at
    a HITL boundary or has completed.  github_token travels via
    config["configurable"] only — never in state.
    """
    logger.info("[runs] _run_graph_background STARTED — run_id=%s", run_id)
    try:
        graph = await get_compiled_graph()
        config = _graph_run_config(run_id, github_token)
        logger.info("[runs] Starting graph.astream — run_id=%s", run_id)
        await _astream_with_retry(graph, initial_state, config, run_id)
        logger.info("[runs] graph.astream ended — run_id=%s", run_id)

        snapshot = await graph.aget_state(config)
        paused = _paused_hitl_node(snapshot)
        logger.info("[runs] aget_state snapshot.next=%s — run_id=%s", snapshot.next, run_id)
        if paused:
            values = dict(snapshot.values) if isinstance(snapshot.values, dict) else {}
            await _handle_hitl_pause(run_id, paused, values)
        else:
            logger.info("[runs] Graph stream completed — run_id=%s", run_id)
    except Exception as exc:
        logger.error("[runs] Graph stream error — run_id=%s: %s", run_id, exc, exc_info=True)
        try:
            # Only mark failed if the run is still in a transient state.
            # Never clobber awaiting_approval (set by _handle_hitl_pause) or terminal states.
            current = await get_run(run_id)
            current_status = current.get("status") if current else "unknown"
            logger.info("[runs] Exception handler — current_status=%s run_id=%s", current_status, run_id)
            if current and current.get("status") not in (
                "awaiting_approval", "completed", "cancelled"
            ):
                await update_run_status(run_id, "failed", "unknown", error=str(exc))
                logger.info("[runs] Set run to failed — run_id=%s", run_id)
            else:
                logger.info("[runs] NOT setting to failed (status protected) — run_id=%s", run_id)
        except Exception:
            pass


async def _resume_graph_background(run_id: str, github_token: str) -> None:
    """
    Resume a paused graph after a HITL approval as a background task.

    github_token must be re-supplied by the approve_run endpoint because
    it is not stored in state or the DB checkpointer.  After astream ends,
    aget_state checks whether the graph paused at a second HITL boundary.
    """
    try:
        graph = await get_compiled_graph()
        config = _graph_run_config(run_id, github_token)
        await _astream_with_retry(graph, None, config, run_id)

        snapshot = await graph.aget_state(config)
        paused = _paused_hitl_node(snapshot)
        if paused:
            values = dict(snapshot.values) if isinstance(snapshot.values, dict) else {}
            await _handle_hitl_pause(run_id, paused, values)
        else:
            logger.info("[runs] Graph resumed and completed — run_id=%s", run_id)
    except Exception as exc:
        logger.error("[runs] Graph resume error — run_id=%s: %s", run_id, exc, exc_info=True)
        try:
            current = await get_run(run_id)
            if current and current.get("status") not in (
                "awaiting_approval", "completed", "cancelled"
            ):
                await update_run_status(run_id, "failed", "unknown", error=str(exc))
        except Exception:
            pass


def _schedule_pipeline(
    run_id: str,
    github_token: str,
    initial_state: Optional[dict[str, Any]] = None,
) -> None:
    """
    Start or resume the graph without holding the HTTP response open.

    FastAPI BackgroundTasks run inside the ASGI request lifecycle. Modal does
    not flush the Start Run response until that lifecycle ends, so the browser
    hits the 15s abort and never receives run_id — the Planner looks like it
    never started. Spawn a dedicated Modal function when available; otherwise
    use asyncio.create_task so the 201 returns immediately.
    """
    try:
        import modal

        fn = modal.Function.from_name("prism", "run_pipeline")
        fn.spawn(run_id, github_token, initial_state)
        logger.info(
            "[runs] Spawned Modal run_pipeline — run_id=%s resume=%s",
            run_id,
            initial_state is None,
        )
        return
    except Exception as exc:
        logger.warning(
            "[runs] Modal spawn unavailable (%s) — using in-process task",
            type(exc).__name__,
        )

    coro = (
        _resume_graph_background(run_id, github_token)
        if initial_state is None
        else _run_graph_background(run_id, initial_state, github_token)
    )
    asyncio.get_running_loop().create_task(coro)


async def execute_pipeline_job(
    run_id: str,
    github_token: str,
    initial_state: Optional[dict[str, Any]] = None,
) -> None:
    """Entry point for the Modal run_pipeline worker."""
    configure_langsmith_tracing()
    try:
        if initial_state is None:
            await _resume_graph_background(run_id, github_token)
            return
        await _run_graph_background(run_id, initial_state, github_token)
    finally:
        flush_langsmith_traces()


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.post(
    "/runs/start",
    response_model=StartRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new Prism pipeline run",
)
async def start_run(
    body: StartRunRequest,
) -> StartRunResponse:
    """
    Validate inputs, create a run record, seed PrismState, and fire the graph
    off-request. Returns run_id immediately so the client can subscribe
    to Supabase Realtime before the first agent event arrives.
    """
    logger.info(
        "[runs] POST /start repo=%s token=%s",
        body.repo_url,
        _redact_token(body.github_token),
    )

    try:
        run_id = await create_run(
            repo_url=body.repo_url,
            issue_url=body.issue_url,
            issue_text=body.issue_text,
            github_token_hint=body.github_token,
        )
    except Exception as exc:
        logger.error("[runs] Failed to create run record: %s", exc, exc_info=True)
        raise _error_response(
            "db_error",
            "Failed to initialise run",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            details={"exception_type": type(exc).__name__},
        )

    # github_token is intentionally absent from initial_state; agents read it from
    # config["configurable"]["github_token"] which is passed to astream below.
    initial_state: PrismState = {
        "repo_url": body.repo_url,
        "issue_url": body.issue_url,
        "issue_text": body.issue_text,
        "run_id": run_id,
        "repo_tree": [],
        "subtasks": [],
        "planner_approved": False,
        "file_map": {},
        "file_contents": {},
        "implementation_plan": [],
        "impl_approved": False,
        "test_results": None,
        "all_tests_passed": False,
        "debug_report": None,
        "pr_draft": None,
        "current_agent": "planner",
        "error": None,
        "messages": [],
    }

    _schedule_pipeline(run_id, body.github_token, dict(initial_state))
    logger.info("[runs] Run %s started — pipeline queued", run_id)

    return StartRunResponse(run_id=run_id, status="running", current_agent="planner")


@router.get(
    "/runs/{run_id}/status",
    response_model=RunStatusResponse,
    summary="Get current status of a run",
)
async def get_run_status(run_id: str) -> RunStatusResponse:
    """Polling fallback for status. Realtime is primary for live UI updates."""
    logger.info("[runs] GET /status run_id=%s", run_id)

    try:
        run = await get_run(run_id)
    except Exception as exc:
        logger.error("[runs] DB error fetching run %s: %s", run_id, exc, exc_info=True)
        raise _error_response(
            "db_error", "Failed to fetch run status", status.HTTP_500_INTERNAL_SERVER_ERROR, run_id
        )

    if run is None:
        raise _error_response("not_found", f"Run {run_id} not found", status.HTTP_404_NOT_FOUND, run_id)

    run = await _reconcile_hitl_pause(run_id, run)

    return RunStatusResponse(
        run_id=run_id,
        status=run.get("status", "unknown"),
        current_agent=run.get("current_agent", "unknown"),
        error=run.get("error"),
        all_tests_passed=run.get("all_tests_passed"),
        updated_at=str(run.get("updated_at", "")),
    )


@router.get(
    "/runs/{run_id}/output",
    response_model=RunOutputResponse,
    summary="Get full accumulated pipeline output",
)
async def get_run_output(run_id: str) -> RunOutputResponse:
    """
    Returns the complete materialised state. file_contents is intentionally
    omitted from this response — it can be large and is available per-file
    via the file_map paths if a future endpoint needs it.
    """
    logger.info("[runs] GET /output run_id=%s", run_id)

    try:
        run = await get_run(run_id)
        outputs = await get_run_outputs(run_id)
    except Exception as exc:
        logger.error("[runs] DB error fetching output for run %s: %s", run_id, exc, exc_info=True)
        raise _error_response(
            "db_error", "Failed to fetch run output", status.HTTP_500_INTERNAL_SERVER_ERROR, run_id
        )

    if run is None:
        raise _error_response("not_found", f"Run {run_id} not found", status.HTTP_404_NOT_FOUND, run_id)

    run = await _reconcile_hitl_pause(run_id, run)

    state_snapshot = await _materialize_run_state(run_id, outputs)
    all_tests_passed = run.get("all_tests_passed")
    if all_tests_passed is None:
        all_tests_passed = state_snapshot.get("all_tests_passed")

    return RunOutputResponse(
        run_id=run_id,
        status=run.get("status", "unknown"),
        current_agent=run.get("current_agent", "unknown"),
        repo_url=run.get("repo_url", ""),
        issue_url=run.get("issue_url"),
        issue_text=run.get("issue_text"),
        subtasks=state_snapshot.get("subtasks") or [],
        planner_approved=state_snapshot.get("planner_approved"),
        file_map=state_snapshot.get("file_map") or {},
        implementation_plan=state_snapshot.get("implementation_plan") or [],
        impl_approved=state_snapshot.get("impl_approved"),
        test_results=state_snapshot.get("test_results"),
        all_tests_passed=all_tests_passed,
        debug_report=state_snapshot.get("debug_report"),
        pr_draft=state_snapshot.get("pr_draft"),
        messages=state_snapshot.get("messages") or [],
        error=run.get("error"),
        pr_url=run.get("pr_url"),
    )


@router.post(
    "/runs/{run_id}/approve",
    response_model=ApproveRunResponse,
    summary="Resume graph after HITL checkpoint",
)
async def approve_run(
    run_id: str,
    body: ApproveRunRequest,
) -> ApproveRunResponse:
    """
    Validates the run is awaiting approval at the correct checkpoint,
    resolves the DB checkpoint record, injects user decision into graph state,
    and resumes execution. github_token is passed only via config, never state.
    """
    logger.info(
        "[runs] POST /approve run_id=%s checkpoint=%s action=%s token=%s",
        run_id,
        body.checkpoint,
        body.action,
        _redact_token(body.github_token),
    )

    try:
        run = await get_run(run_id)
    except Exception as exc:
        logger.error("[runs] DB error fetching run for approve %s: %s", run_id, exc, exc_info=True)
        raise _error_response(
            "db_error", "Failed to fetch run", status.HTTP_500_INTERNAL_SERVER_ERROR, run_id
        )

    if run is None:
        raise _error_response("not_found", f"Run {run_id} not found", status.HTTP_404_NOT_FOUND, run_id)

    run = await _reconcile_hitl_pause(run_id, run)

    if run.get("status") != "awaiting_approval":
        raise _error_response(
            "invalid_state",
            f"Run is not awaiting approval (status={run.get('status')})",
            status.HTTP_409_CONFLICT,
            run_id,
        )

    if run.get("current_agent") != body.checkpoint:
        raise _error_response(
            "checkpoint_mismatch",
            f"Expected checkpoint {run.get('current_agent')}, got {body.checkpoint}",
            status.HTTP_409_CONFLICT,
            run_id,
        )

    # ── Handle stop / restart ──────────────────────────────────────────────────
    if body.action in ("stop", "restart"):
        try:
            await resolve_checkpoint(run_id, body.checkpoint, {"action": body.action})
            await save_agent_output(run_id, body.checkpoint, {"action": body.action}, "complete")
            await update_run_status(run_id, "cancelled", body.checkpoint)
        except Exception as exc:
            logger.error("[runs] Failed to cancel run %s: %s", run_id, exc)
        msg = (
            "Run cancelled by user"
            if body.action == "stop"
            else "Run cancelled. Call POST /api/runs/start to begin a new run."
        )
        return ApproveRunResponse(
            run_id=run_id,
            status="cancelled",
            current_agent=body.checkpoint,
            message=msg,
        )

    next_agent = "code_navigator" if body.checkpoint == "hitl_1" else "test_runner"

    # ── Resolve checkpoint in DB (HITL node code after interrupt() is unreachable) ──
    try:
        await resolve_checkpoint(run_id, body.checkpoint, {"action": body.action})
        await save_agent_output(run_id, body.checkpoint, {"action": body.action}, "complete")
        # Advance current_agent past the HITL node so the UI does not keep
        # showing Checkpoint 1 as the active running step.
        await update_run_status(run_id, "running", next_agent, error="")
    except Exception as exc:
        logger.warning(
            "[runs] Could not resolve checkpoint %s for run %s: %s",
            body.checkpoint,
            run_id,
            exc,
        )

    # ── Build state_update — NO github_token; that travels via config only ─────
    if body.checkpoint == "hitl_1":
        state_update: dict[str, Any] = {
            "planner_approved": True,
            "current_agent": "hitl_1",
            "messages": [f"[hitl_1] Checkpoint approved — action={body.action}"],
        }
        if body.subtasks is not None:
            state_update["subtasks"] = body.subtasks
    else:  # hitl_2
        state_update = {
            "impl_approved": True,
            "current_agent": "hitl_2",
            "messages": [f"[hitl_2] Checkpoint approved — action={body.action}"],
        }
        if body.implementation_plan is not None:
            state_update["implementation_plan"] = body.implementation_plan

    try:
        graph = await get_compiled_graph()
        config = {"configurable": {"thread_id": run_id}}
        await graph.aupdate_state(config, state_update, as_node=body.checkpoint)
    except Exception as exc:
        logger.error("[runs] Failed to update graph state for run %s: %s", run_id, exc, exc_info=True)
        raise _error_response(
            "graph_error",
            "Failed to inject approval into graph",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            run_id,
        )
    # github_token is passed here so agents after resume can access it via config
    _schedule_pipeline(run_id, body.github_token, None)

    return ApproveRunResponse(
        run_id=run_id,
        status="running",
        current_agent=next_agent,
        message=f"Resumed after {body.checkpoint}",
    )


@router.post(
    "/runs/{run_id}/create-pr",
    response_model=CreatePRResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a GitHub pull request from the PR draft",
)
async def create_pr(run_id: str, body: CreatePRRequest) -> CreatePRResponse:
    """
    Creates a GitHub PR using the pr_draft produced by the PR Summarizer.

    PR strategy (API.md §2.5): Create branch prism/<run_id>, commit a
    PRISM_REPORT.md containing the full plan + test + debug + draft, then
    open a PR against the repo's default branch. This satisfies the Option B
    "real PR on GitHub" requirement without claiming Prism wrote application code.
    """
    logger.info(
        "[runs] POST /create-pr run_id=%s token=%s",
        run_id,
        _redact_token(body.github_token),
    )

    try:
        run = await get_run(run_id)
        outputs = await get_run_outputs(run_id)
    except Exception as exc:
        logger.error("[runs] DB error for create-pr run %s: %s", run_id, exc, exc_info=True)
        raise _error_response(
            "db_error", "Failed to fetch run data", status.HTTP_500_INTERNAL_SERVER_ERROR, run_id
        )

    if run is None:
        raise _error_response("not_found", f"Run {run_id} not found", status.HTTP_404_NOT_FOUND, run_id)

    if run.get("status") != "completed":
        raise _error_response(
            "invalid_state",
            f"Run must be completed to create a PR (status={run.get('status')})",
            status.HTTP_409_CONFLICT,
            run_id,
        )

    state_snapshot = await _materialize_run_state(run_id, outputs)
    pr_draft = state_snapshot.get("pr_draft")
    if not isinstance(pr_draft, dict) or not pr_draft.get("title"):
        raise _error_response(
            "missing_draft",
            "PR draft not found in run outputs",
            status.HTTP_409_CONFLICT,
            run_id,
        )

    branch_name = body.head_branch or f"prism/{run_id[:8]}"

    # ── Build PRISM_REPORT.md content ──────────────────────────────────────────

    report_md = _build_prism_report(run_id, run, state_snapshot, pr_draft)

    try:
        github_client = get_github_client(body.github_token)
        repo = get_repo(github_client, run["repo_url"])

        requested_base = body.base_branch
        base_branch = requested_base or repo.default_branch
        try:
            create_branch(repo, branch_name, base=base_branch)
        except GithubException as exc:
            default_branch = repo.default_branch
            if (
                requested_base
                and exc.status == 404
                and requested_base != default_branch
            ):
                logger.warning(
                    "[runs] base branch %s not found for run %s; retrying with %s",
                    requested_base,
                    run_id,
                    default_branch,
                )
                base_branch = default_branch
                create_branch(repo, branch_name, base=base_branch)
            else:
                raise

        commit_msg = body.commit_message or f"chore: add Prism analysis report for run {run_id[:8]}"
        commit_file(repo, branch_name, "PRISM_REPORT.md", report_md, commit_msg)

        pr_url = create_pull_request(
            repo=repo,
            title=pr_draft.get("title", f"Prism Analysis — {run_id[:8]}"),
            body=_build_pr_body(pr_draft),
            head=branch_name,
            base=base_branch,
        )
    except GithubException as exc:
        logger.error("[runs] GitHub error creating PR for run %s: %s", run_id, exc, exc_info=True)
        raise _error_response(
            "github_error",
            format_github_write_error(exc),
            status.HTTP_502_BAD_GATEWAY,
            run_id,
            details={"github_status": exc.status},
        )
    except Exception as exc:
        logger.error("[runs] GitHub error creating PR for run %s: %s", run_id, exc, exc_info=True)
        raise _error_response(
            "github_error",
            "GitHub could not create the pull request. Try again, or open the PR manually from the draft.",
            status.HTTP_502_BAD_GATEWAY,
            run_id,
        )

    # Extract PR number from URL for the response
    pr_number_match = re.search(r"/pull/(\d+)$", pr_url)
    pr_number = int(pr_number_match.group(1)) if pr_number_match else None

    try:
        await update_run_status(run_id, "completed", "pr_summarizer", pr_url=pr_url)
    except Exception as exc:
        logger.warning("[runs] Could not update pr_url in run record: %s", exc)

    return CreatePRResponse(
        run_id=run_id,
        pr_url=pr_url,
        pr_number=pr_number,
        title=pr_draft.get("title", ""),
    )


# ── Report builders ────────────────────────────────────────────────────────────

def _build_pr_body(pr_draft: dict[str, Any]) -> str:
    """Format the pr_draft fields into a full GitHub PR markdown body."""
    checklist = "\n".join(
        f"- [ ] {item}" for item in pr_draft.get("review_checklist", [])
    )
    return f"""{pr_draft.get('body', '')}

---

## What Changed

{pr_draft.get('what_changed', '')}

## Why

{pr_draft.get('why', '')}

## Testing Notes

{pr_draft.get('testing_notes', '')}

## Known Limitations

{pr_draft.get('limitations', '')}

## Review Checklist

{checklist}

---
*Generated by [Prism](https://github.com/prism-ai/prism) — multi-agent software engineering teammate.*
"""


def _build_prism_report(
    run_id: str,
    run: dict[str, Any],
    state: dict[str, Any],
    pr_draft: dict[str, Any],
) -> str:
    """Build the PRISM_REPORT.md committed to the branch."""
    subtasks = state.get("subtasks", [])
    subtasks_md = "\n".join(
        f"- **[{st.get('complexity', '').upper()}]** {st.get('title', '')}: {st.get('description', '')}"
        for st in subtasks
    )

    test_results = state.get("test_results", {}) or {}
    test_md = (
        f"Framework: {test_results.get('framework', 'N/A')} | "
        f"Passed: {test_results.get('passed_count', 0)} | "
        f"Failed: {test_results.get('failed_count', 0)}"
        if test_results
        else "Test results not available"
    )

    debug_report = state.get("debug_report", {}) or {}
    debug_md = debug_report.get("summary", "No debug analysis performed")

    checklist = "\n".join(
        f"- [ ] {item}" for item in pr_draft.get("review_checklist", [])
    )

    return f"""# Prism Analysis Report

**Run ID:** `{run_id}`
**Repository:** {run.get('repo_url', '')}
**Issue:** {run.get('issue_url') or 'Pasted text'}

---

## PR Summary

**{pr_draft.get('title', '')}**

{pr_draft.get('body', '')}

---

## Subtask Breakdown

{subtasks_md or 'No subtasks available'}

---

## What Changed

{pr_draft.get('what_changed', '')}

## Why

{pr_draft.get('why', '')}

---

## Test Results

{test_md}

## Debug Analysis

{debug_md}

---

## Testing Notes for Reviewers

{pr_draft.get('testing_notes', '')}

## Known Limitations

{pr_draft.get('limitations', '')}

## Review Checklist

{checklist}

---
*Generated by [Prism](https://github.com/prism-ai/prism)*
"""
