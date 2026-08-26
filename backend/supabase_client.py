"""
backend/supabase_client.py — Supabase client and all database helper functions.

All public functions are async. They delegate blocking network I/O to a thread
pool via asyncio.to_thread so the FastAPI/LangGraph event loop is never blocked.

The Supabase service-role client is used for all backend operations.
Realtime updates are emitted implicitly: Supabase broadcasts any INSERT/UPDATE
on tables that have Realtime enabled, so frontend subscribers receive live
updates automatically when this module writes to runs or agent_outputs.

SECURITY: github_token is NEVER written by any function in this module.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional, TypeVar

import httpx
from supabase import Client, ClientOptions, create_client

from backend.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# postgrest-py defaults to http2=True. Cloudflare in front of Supabase REST
# then emits HTTP/2 ConnectionTerminated (error_code 8=CANCEL, 9=COMPRESSION_ERROR),
# which previously aborted the graph after Test Runner and left pr_draft empty.
_SUPABASE_HTTP_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# ── Singleton client ───────────────────────────────────────────────────────────
_supabase: Optional[Client] = None


def is_transient_http_error(exc: BaseException) -> bool:
    """True for dropped HTTP/2 streams and similar short-lived transport failures."""
    names = {
        "ConnectionTerminated",
        "RemoteProtocolError",
        "LocalProtocolError",
        "ConnectError",
        "ReadError",
        "WriteError",
        "PoolTimeout",
        "ReadTimeout",
        "ConnectTimeout",
    }
    current: Optional[BaseException] = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ in names:
            return True
        text = str(current)
        if "ConnectionTerminated" in text or "Server disconnected" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


async def _to_thread_retry(fn: Callable[[], T], *, attempts: int = 3) -> T:
    """Run blocking Supabase I/O off the event loop, retrying transient HTTP drops."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.to_thread(fn)
        except Exception as exc:
            last_exc = exc
            if not is_transient_http_error(exc) or attempt == attempts:
                raise
            logger.warning(
                "Supabase HTTP retry %s/%s after transient error: %s",
                attempt,
                attempts,
                exc,
            )
            await asyncio.sleep(0.5 * attempt)
    raise RuntimeError("Supabase retry exhausted") from last_exc


def get_supabase() -> Client:
    """Return the singleton Supabase service-role client, creating it if needed."""
    global _supabase
    if _supabase is None:
        http_client = httpx.Client(
            http2=False,
            timeout=_SUPABASE_HTTP_TIMEOUT,
            follow_redirects=True,
        )
        _supabase = create_client(
            settings.supabase_url,
            settings.supabase_service_key,
            options=ClientOptions(httpx_client=http_client),
        )
        logger.info("Supabase client initialised — url=%s http2=False", settings.supabase_url)
    return _supabase


# ── runs table ────────────────────────────────────────────────────────────────

async def create_run(
    repo_url: str,
    issue_url: Optional[str],
    issue_text: Optional[str],
    github_token_hint: str,
) -> str:
    """
    Insert a new run record and return the generated run_id (UUID).

    github_token_hint must be the LAST FOUR CHARACTERS of the PAT only.
    The full token is never stored.
    """
    run_id = str(uuid.uuid4())
    client = get_supabase()
    insert_data: dict[str, Any] = {
        "id": run_id,
        "repo_url": repo_url,
        "issue_url": issue_url,
        "issue_text": issue_text,
        "github_token_hint": github_token_hint[-4:] if github_token_hint else "",
        "status": "running",
        "current_agent": "planner",
    }
    try:
        await _to_thread_retry(
            lambda: client.table("runs").insert(insert_data).execute()
        )
        logger.info("Created run %s for repo %s", run_id, repo_url)
        return run_id
    except Exception as exc:
        logger.error("Supabase error creating run: %s", exc, exc_info=True)
        raise


async def update_run_status(
    run_id: str,
    status: str,
    current_agent: str,
    error: Optional[str] = None,
    all_tests_passed: Optional[bool] = None,
    pr_url: Optional[str] = None,
) -> None:
    """Upsert run status — triggers Supabase Realtime broadcast to subscribers."""
    client = get_supabase()
    payload: dict[str, Any] = {
        "status": status,
        "current_agent": current_agent,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error is not None:
        payload["error"] = error
    if all_tests_passed is not None:
        payload["all_tests_passed"] = all_tests_passed
    if pr_url is not None:
        payload["pr_url"] = pr_url
    logger.info(
        "update_run_status run_id=%s status=%s agent=%s error=%s",
        run_id, status, current_agent, error
    )
    try:
        await _to_thread_retry(
            lambda: client.table("runs").update(payload).eq("id", run_id).execute()
        )
    except Exception as exc:
        logger.error("Supabase error updating run %s status: %s", run_id, exc, exc_info=True)
        raise


# ── agent_outputs table ────────────────────────────────────────────────────────

async def save_agent_output(
    run_id: str,
    agent: str,
    payload: dict[str, Any],
    phase: str,
) -> None:
    """
    Insert a row into agent_outputs for a start or complete event.

    phase must be "start" or "complete". Supabase Realtime broadcasts
    this insert to all clients subscribed to the run_id channel.
    """
    client = get_supabase()
    insert_data: dict[str, Any] = {
        "run_id": run_id,
        "agent": agent,
        "phase": phase,
        "payload": payload,
    }
    try:
        await _to_thread_retry(
            lambda: client.table("agent_outputs").insert(insert_data).execute()
        )
    except Exception as exc:
        logger.error(
            "Supabase error saving agent output run=%s agent=%s phase=%s: %s",
            run_id,
            agent,
            phase,
            exc,
            exc_info=True,
        )
        raise


# ── hitl_checkpoints table ─────────────────────────────────────────────────────
# Named hitl_checkpoints (not checkpoints) to avoid collision with LangGraph's
# own checkpoints table created by AsyncPostgresSaver.setup().

async def create_checkpoint(
    run_id: str,
    checkpoint_name: str,
    payload: dict[str, Any],
) -> str:
    """Write a HITL checkpoint record and return its id."""
    checkpoint_id = str(uuid.uuid4())
    client = get_supabase()
    insert_data: dict[str, Any] = {
        "id": checkpoint_id,
        "run_id": run_id,
        "checkpoint_name": checkpoint_name,
        "payload": payload,
    }
    try:
        await _to_thread_retry(
            lambda: client.table("hitl_checkpoints").insert(insert_data).execute()
        )
        logger.info("Created checkpoint %s for run %s", checkpoint_name, run_id)
        return checkpoint_id
    except Exception as exc:
        logger.error(
            "Supabase error creating checkpoint %s for run %s: %s",
            checkpoint_name,
            run_id,
            exc,
            exc_info=True,
        )
        raise


async def resolve_checkpoint(
    run_id: str,
    checkpoint_name: str,
    user_decision: dict[str, Any],
) -> None:
    """Record the user's decision on a HITL checkpoint and set resolved_at."""
    client = get_supabase()
    update_data: dict[str, Any] = {
        "user_decision": user_decision,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await _to_thread_retry(
            lambda: (
                client.table("hitl_checkpoints")
                .update(update_data)
                .eq("run_id", run_id)
                .eq("checkpoint_name", checkpoint_name)
                .is_("resolved_at", "null")
                .execute()
            )
        )
        logger.info("Resolved checkpoint %s for run %s", checkpoint_name, run_id)
    except Exception as exc:
        logger.error(
            "Supabase error resolving checkpoint %s for run %s: %s",
            checkpoint_name,
            run_id,
            exc,
            exc_info=True,
        )
        raise


async def get_checkpoint(run_id: str, checkpoint_name: str) -> Optional[dict[str, Any]]:
    """Fetch the most recent unresolved checkpoint record."""
    client = get_supabase()
    try:
        result = await _to_thread_retry(
            lambda: (
                client.table("hitl_checkpoints")
                .select("*")
                .eq("run_id", run_id)
                .eq("checkpoint_name", checkpoint_name)
                .is_("resolved_at", "null")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
        )
        if result.data:
            return result.data[0]  # type: ignore[return-value]
        return None
    except Exception as exc:
        logger.error("Supabase error fetching checkpoint: %s", exc, exc_info=True)
        raise


# ── runs read helpers ──────────────────────────────────────────────────────────

async def get_run(run_id: str) -> Optional[dict[str, Any]]:
    """Fetch a single run record by id."""
    client = get_supabase()
    try:
        result = await _to_thread_retry(
            lambda: client.table("runs").select("*").eq("id", run_id).execute()
        )
        if result.data:
            return result.data[0]  # type: ignore[return-value]
        return None
    except Exception as exc:
        logger.error("Supabase error fetching run %s: %s", run_id, exc, exc_info=True)
        raise


async def get_run_outputs(run_id: str) -> list[dict[str, Any]]:
    """Fetch all agent_output rows for a run, ordered by creation time."""
    client = get_supabase()
    try:
        result = await _to_thread_retry(
            lambda: (
                client.table("agent_outputs")
                .select("*")
                .eq("run_id", run_id)
                .order("created_at")
                .execute()
            )
        )
        return result.data or []  # type: ignore[return-value]
    except Exception as exc:
        logger.error("Supabase error fetching outputs for run %s: %s", run_id, exc, exc_info=True)
        raise


# ── repo_cache table ───────────────────────────────────────────────────────────

async def get_repo_cache(repo_url: str) -> Optional[dict[str, Any]]:
    """Return cached repo metadata or None if no cache entry exists."""
    client = get_supabase()
    try:
        result = await _to_thread_retry(
            lambda: client.table("repo_cache").select("*").eq("repo_url", repo_url).execute()
        )
        if result.data:
            return result.data[0]  # type: ignore[return-value]
        return None
    except Exception as exc:
        logger.error("Supabase error fetching repo cache for %s: %s", repo_url, exc, exc_info=True)
        raise


async def upsert_repo_cache(
    repo_url: str,
    file_tree: list[str],
    embedding_count: int,
) -> None:
    """Insert or update the repo cache entry with the latest file tree."""
    client = get_supabase()
    upsert_data: dict[str, Any] = {
        "repo_url": repo_url,
        "file_tree": file_tree,
        "embedding_count": embedding_count,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await _to_thread_retry(
            lambda: client.table("repo_cache").upsert(
                upsert_data, on_conflict="repo_url"
            ).execute()
        )
        logger.info("Upserted repo cache for %s (%d embeddings)", repo_url, embedding_count)
    except Exception as exc:
        logger.error("Supabase error upserting repo cache: %s", exc, exc_info=True)
        raise


# ── code_embeddings table ──────────────────────────────────────────────────────

async def save_code_embeddings(chunks: list[dict[str, Any]]) -> None:
    """
    Batch-insert code embedding chunks.

    Each chunk dict must contain:
      repo_url, file_path, chunk_index, chunk_text, embedding (list[float]),
      token_count, metadata (dict)
    """
    if not chunks:
        return
    client = get_supabase()
    try:
        await _to_thread_retry(
            lambda: client.table("code_embeddings").insert(chunks).execute()
        )
        logger.info("Saved %d code embedding chunks", len(chunks))
    except Exception as exc:
        logger.error("Supabase error saving code embeddings: %s", exc, exc_info=True)
        raise


async def search_code_embeddings(
    repo_url: str,
    query_vector: list[float],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Semantic search over code_embeddings using pgvector cosine similarity.

    Calls a Supabase RPC function `match_code_embeddings` that wraps the
    `<=>` cosine distance operator for the given repo.
    """
    client = get_supabase()
    rpc_args: dict[str, Any] = {
        "query_embedding": query_vector,
        "match_repo_url": repo_url,
        "match_count": limit,
    }
    try:
        result = await _to_thread_retry(
            lambda: client.rpc("match_code_embeddings", rpc_args).execute()
        )
        return result.data or []  # type: ignore[return-value]
    except Exception as exc:
        logger.error("Supabase error searching embeddings: %s", exc, exc_info=True)
        raise
