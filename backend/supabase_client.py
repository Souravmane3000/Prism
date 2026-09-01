"""
backend/supabase_client.py — Supabase client and all database helper functions.

All public functions are async. They delegate blocking network I/O to a thread
pool via asyncio.to_thread so the FastAPI/LangGraph event loop is never blocked.

The Supabase service-role REST client is used first. If Cloudflare in front of
*.supabase.co returns 522 HTML (common from Modal), we write through the IPv4
session pooler instead. Realtime still fires from WAL on those SQL inserts.

SECURITY: github_token is NEVER written by any function in this module.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional, TypeVar

import httpx
from psycopg.types.json import Jsonb
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


_GATEWAY_STATUS_CODES = {"520", "521", "522", "523", "524", "525", "526", "527"}


def is_rest_gateway_error(exc: BaseException) -> bool:
    """True when Cloudflare/HTML sits in front of PostgREST instead of JSON."""
    code = getattr(exc, "code", None)
    if code is not None and str(code) in _GATEWAY_STATUS_CODES:
        return True
    text = str(exc)
    lowered = text.lower()
    if "json could not be generated" in lowered:
        return True
    if "cloudflare" in lowered or "error code 522" in lowered:
        return True
    if "<!doctype html" in lowered:
        return True
    return False


def should_use_sql_fallback(exc: BaseException) -> bool:
    """REST from Modal often 522s; the IPv4 session pooler is the working path."""
    if is_rest_gateway_error(exc) or is_transient_http_error(exc):
        return True
    text = str(exc).lower()
    return (
        "connection refused" in text
        or "name or service not known" in text
        or "failed to resolve" in text
    )


def _jsonable_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            out[key] = str(value)
        else:
            out[key] = value
    return out


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in values) + "]"


async def _sql_conn() -> Any:
    """Open a short-lived pooler connection. Tests patch _sql_execute/_sql_fetch*."""
    import psycopg
    from psycopg.rows import dict_row

    from backend.config import postgres_connect_kwargs

    return await psycopg.AsyncConnection.connect(
        **postgres_connect_kwargs(),
        row_factory=dict_row,
    )


async def _sql_execute(query: str, params: tuple[Any, ...] = ()) -> None:
    conn = await _sql_conn()
    try:
        await conn.execute(query, params)
    finally:
        await conn.close()


async def _sql_fetchone(
    query: str, params: tuple[Any, ...] = ()
) -> Optional[dict[str, Any]]:
    conn = await _sql_conn()
    try:
        result = await conn.execute(query, params)
        row = await result.fetchone()
        return dict(row) if row is not None else None
    finally:
        await conn.close()


async def _sql_fetchall(
    query: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    conn = await _sql_conn()
    try:
        result = await conn.execute(query, params)
        rows = await result.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def _sql_executemany(query: str, params_seq: list[tuple[Any, ...]]) -> None:
    conn = await _sql_conn()
    try:
        await conn.executemany(query, params_seq)
    finally:
        await conn.close()


async def ping_postgres() -> None:
    """Startup check that does not go through Cloudflare REST."""
    row = await _sql_fetchone("SELECT 1 AS ok")
    if not row or row.get("ok") != 1:
        raise RuntimeError("Postgres pooler ping returned no row")


async def _with_sql_fallback(
    rest_fn: Callable[[], T],
    sql_fn: Callable[[], Any],
) -> T:
    try:
        return await _to_thread_retry(rest_fn)
    except Exception as exc:
        if not should_use_sql_fallback(exc):
            raise
        logger.warning(
            "Supabase REST unavailable (%s) — using Postgres pooler",
            type(exc).__name__,
        )
        return await sql_fn()  # type: ignore[no-any-return]


async def _to_thread_retry(fn: Callable[[], T], *, attempts: int = 3) -> T:
    """Run blocking Supabase I/O off the event loop, retrying transient HTTP drops."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.to_thread(fn)
        except Exception as exc:
            last_exc = exc
            if is_rest_gateway_error(exc):
                raise
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
        await _with_sql_fallback(
            lambda: client.table("runs").insert(insert_data).execute(),
            lambda: _sql_execute(
                """
                INSERT INTO runs (
                    id, repo_url, issue_url, issue_text,
                    github_token_hint, status, current_agent
                )
                VALUES (%s, %s, %s, %s, %s, 'running', 'planner')
                """,
                (
                    run_id,
                    repo_url,
                    issue_url,
                    issue_text,
                    insert_data["github_token_hint"],
                ),
            ),
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
        await _with_sql_fallback(
            lambda: client.table("runs").update(payload).eq("id", run_id).execute(),
            lambda: _sql_execute(
                """
                UPDATE runs
                SET status = %s,
                    current_agent = %s,
                    updated_at = %s,
                    error = COALESCE(%s, error),
                    all_tests_passed = COALESCE(%s, all_tests_passed),
                    pr_url = COALESCE(%s, pr_url)
                WHERE id = %s
                """,
                (
                    payload["status"],
                    payload["current_agent"],
                    payload["updated_at"],
                    payload.get("error"),
                    payload.get("all_tests_passed"),
                    payload.get("pr_url"),
                    run_id,
                ),
            ),
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
        await _with_sql_fallback(
            lambda: client.table("agent_outputs").insert(insert_data).execute(),
            lambda: _sql_execute(
                """
                INSERT INTO agent_outputs (run_id, agent, phase, payload)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    run_id,
                    agent,
                    phase,
                    Jsonb(payload),
                ),
            ),
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
        await _with_sql_fallback(
            lambda: client.table("hitl_checkpoints").insert(insert_data).execute(),
            lambda: _sql_execute(
                """
                INSERT INTO hitl_checkpoints (id, run_id, checkpoint_name, payload)
                VALUES (%s, %s, %s, %s)
                """,
                (checkpoint_id, run_id, checkpoint_name, Jsonb(payload)),
            ),
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
        await _with_sql_fallback(
            lambda: (
                client.table("hitl_checkpoints")
                .update(update_data)
                .eq("run_id", run_id)
                .eq("checkpoint_name", checkpoint_name)
                .is_("resolved_at", "null")
                .execute()
            ),
            lambda: _sql_execute(
                """
                UPDATE hitl_checkpoints
                SET user_decision = %s, resolved_at = %s
                WHERE run_id = %s
                  AND checkpoint_name = %s
                  AND resolved_at IS NULL
                """,
                (
                    Jsonb(user_decision),
                    update_data["resolved_at"],
                    run_id,
                    checkpoint_name,
                ),
            ),
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
    def _rest() -> Optional[dict[str, Any]]:
        result = (
            client.table("hitl_checkpoints")
            .select("*")
            .eq("run_id", run_id)
            .eq("checkpoint_name", checkpoint_name)
            .is_("resolved_at", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]  # type: ignore[no-any-return]
        return None

    async def _sql() -> Optional[dict[str, Any]]:
        row = await _sql_fetchone(
            """
            SELECT * FROM hitl_checkpoints
            WHERE run_id = %s AND checkpoint_name = %s AND resolved_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id, checkpoint_name),
        )
        return _jsonable_row(row) if row else None

    try:
        return await _with_sql_fallback(_rest, _sql)
    except Exception as exc:
        logger.error("Supabase error fetching checkpoint: %s", exc, exc_info=True)
        raise


# ── runs read helpers ──────────────────────────────────────────────────────────

async def get_run(run_id: str) -> Optional[dict[str, Any]]:
    """Fetch a single run record by id."""
    client = get_supabase()
    def _rest() -> Optional[dict[str, Any]]:
        result = client.table("runs").select("*").eq("id", run_id).execute()
        if result.data:
            return result.data[0]  # type: ignore[no-any-return]
        return None

    async def _sql() -> Optional[dict[str, Any]]:
        row = await _sql_fetchone("SELECT * FROM runs WHERE id = %s", (run_id,))
        return _jsonable_row(row) if row else None

    try:
        return await _with_sql_fallback(_rest, _sql)
    except Exception as exc:
        logger.error("Supabase error fetching run %s: %s", run_id, exc, exc_info=True)
        raise


async def get_run_outputs(run_id: str) -> list[dict[str, Any]]:
    """Fetch all agent_output rows for a run, ordered by creation time."""
    client = get_supabase()
    def _rest() -> list[dict[str, Any]]:
        result = (
            client.table("agent_outputs")
            .select("*")
            .eq("run_id", run_id)
            .order("created_at")
            .execute()
        )
        return result.data or []  # type: ignore[no-any-return]

    async def _sql() -> list[dict[str, Any]]:
        rows = await _sql_fetchall(
            """
            SELECT * FROM agent_outputs
            WHERE run_id = %s
            ORDER BY created_at
            """,
            (run_id,),
        )
        return [_jsonable_row(row) for row in rows]

    try:
        return await _with_sql_fallback(_rest, _sql)
    except Exception as exc:
        logger.error("Supabase error fetching outputs for run %s: %s", run_id, exc, exc_info=True)
        raise


# ── repo_cache table ───────────────────────────────────────────────────────────

async def get_repo_cache(repo_url: str) -> Optional[dict[str, Any]]:
    """Return cached repo metadata or None if no cache entry exists."""
    client = get_supabase()
    def _rest() -> Optional[dict[str, Any]]:
        result = client.table("repo_cache").select("*").eq("repo_url", repo_url).execute()
        if result.data:
            return result.data[0]  # type: ignore[no-any-return]
        return None

    async def _sql() -> Optional[dict[str, Any]]:
        row = await _sql_fetchone(
            "SELECT * FROM repo_cache WHERE repo_url = %s",
            (repo_url,),
        )
        return _jsonable_row(row) if row else None

    try:
        return await _with_sql_fallback(_rest, _sql)
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
        await _with_sql_fallback(
            lambda: client.table("repo_cache").upsert(
                upsert_data, on_conflict="repo_url"
            ).execute(),
            lambda: _sql_execute(
                """
                INSERT INTO repo_cache (repo_url, file_tree, embedding_count, last_synced_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (repo_url) DO UPDATE SET
                    file_tree = EXCLUDED.file_tree,
                    embedding_count = EXCLUDED.embedding_count,
                    last_synced_at = EXCLUDED.last_synced_at
                """,
                (
                    repo_url,
                    Jsonb(file_tree),
                    embedding_count,
                    upsert_data["last_synced_at"],
                ),
            ),
        )
        logger.info("Upserted repo cache for %s (%d embeddings)", repo_url, embedding_count)
    except Exception as exc:
        logger.error("Supabase error upserting repo cache: %s", exc, exc_info=True)
        raise


# ── code_embeddings table ──────────────────────────────────────────────────────

async def _save_code_embeddings_sql(chunks: list[dict[str, Any]]) -> None:
    rows: list[tuple[Any, ...]] = []
    for chunk in chunks:
        embedding = chunk.get("embedding")
        vec = _vector_literal(embedding) if isinstance(embedding, list) else None
        metadata = chunk.get("metadata") or {}
        rows.append(
            (
                chunk["repo_url"],
                chunk["file_path"],
                chunk["chunk_index"],
                chunk["chunk_text"],
                vec,
                chunk.get("token_count"),
                Jsonb(metadata),
            )
        )
    await _sql_executemany(
        """
        INSERT INTO code_embeddings (
            repo_url, file_path, chunk_index, chunk_text,
            embedding, token_count, metadata
        )
        VALUES (%s, %s, %s, %s, %s::vector, %s, %s)
        """,
        rows,
    )


async def _search_code_embeddings_sql(
    repo_url: str,
    query_vector: list[float],
    limit: int,
) -> list[dict[str, Any]]:
    rows = await _sql_fetchall(
        "SELECT * FROM match_code_embeddings(%s::vector, %s, %s)",
        (_vector_literal(query_vector), repo_url, limit),
    )
    return [_jsonable_row(row) for row in rows]


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
        await _with_sql_fallback(
            lambda: client.table("code_embeddings").insert(chunks).execute(),
            lambda: _save_code_embeddings_sql(chunks),
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
        result = await _with_sql_fallback(
            lambda: client.rpc("match_code_embeddings", rpc_args).execute(),
            lambda: _search_code_embeddings_sql(repo_url, query_vector, limit),
        )
        if hasattr(result, "data"):
            return result.data or []  # type: ignore[no-any-return]
        return result  # type: ignore[no-any-return]
    except Exception as exc:
        logger.error("Supabase error searching embeddings: %s", exc, exc_info=True)
        raise
