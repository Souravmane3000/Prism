"""
backend/agents/code_navigator.py — Code Navigator agent node.

Maps each subtask to the most relevant files in the repository using two
parallel strategies: pgvector semantic search over embedded code chunks, and
GitHub API path/keyword matching. Results are merged and deduplicated.

This agent is tool-heavy and LLM-light. The LLM is not involved in file
selection; ranking is done by embedding similarity and path heuristics.

The github_token is read from config["configurable"]["github_token"], never
from state, so it is never checkpointed to PostgreSQL.
"""

import logging
import re
from typing import Any, Optional

import tiktoken
from github.GithubException import GithubException
from langchain_core.runnables import RunnableConfig
from langchain_openai import OpenAIEmbeddings

from backend.config import settings
from backend.github_client import (
    get_file_content,
    get_github_client,
    get_repo,
)
from backend.state import FileMapEntry, PrismState, Subtask
from backend.supabase_client import (
    get_repo_cache,
    save_agent_output,
    save_code_embeddings,
    search_code_embeddings,
    update_run_status,
    upsert_repo_cache,
)

logger = logging.getLogger(__name__)

# Approximate token limit per chunk (for embedding; not LLM context)
_CHUNK_TOKEN_LIMIT = 400
# Maximum files to fetch full content for (to keep context manageable)
_MAX_FILES_PER_SUBTASK = 5
# Cap GitHub fetches + embeddings. Full-repo embed of FastAPI (~3k files)
# takes 10–20+ minutes because each file is a sequential API call.
_MAX_EMBED_FILES = 150
# Files matching these extensions are skipped during embedding
_SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".mp4", ".mp3", ".zip", ".tar", ".gz", ".pdf",
    ".lock", ".min.js", ".min.css",
}
_SKIP_PATH_PREFIXES = (
    "docs/",
    "node_modules/",
    "dist/",
    "build/",
    "vendor/",
    "__pycache__/",
    ".git/",
    "site-packages/",
)
_SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".rb", ".php", ".cs", ".kt", ".swift", ".toml", ".yml", ".yaml",
}


def _get_embeddings_client() -> OpenAIEmbeddings:
    """
    Return an OpenAIEmbeddings client for text-embedding-3-small.
    Uses the same OPENAI_API_KEY as the chat client per ADR-001.
    """
    return OpenAIEmbeddings(
        api_key=settings.openai_api_key,
        model=settings.openai_embedding_model,
        timeout=60,
    )


def _should_skip_file(path: str) -> bool:
    """Return True for binary/generated files that should not be embedded."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in _SKIP_EXTENSIONS)


def _is_skipped_path(path: str) -> bool:
    """Skip docs translations, vendored trees, and other non-source bulk."""
    normalised = path.replace("\\", "/").lstrip("./")
    lower = normalised.lower()
    return any(lower.startswith(prefix) or f"/{prefix}" in f"/{lower}" for prefix in _SKIP_PATH_PREFIXES)


def _select_embed_files(file_tree: list[str], subtasks: list[Subtask]) -> list[str]:
    """
    Bound the embed set so large repos (FastAPI ~3k paths) finish quickly.

    Always include planner likely_files that exist in the tree, then fill
    remaining slots with source-code paths. Docs/vendor trees are excluded
    unless a subtask explicitly named them.
    """
    tree_set = set(file_tree)
    selected: list[str] = []
    seen: set[str] = set()

    for subtask in subtasks:
        for path in subtask.get("likely_files", []):
            if path in tree_set and path not in seen and not _should_skip_file(path):
                selected.append(path)
                seen.add(path)

    source_candidates = [
        p for p in file_tree
        if p not in seen
        and not _should_skip_file(p)
        and not _is_skipped_path(p)
        and any(p.lower().endswith(ext) for ext in _SOURCE_EXTENSIONS)
    ]
    for path in source_candidates:
        if len(selected) >= _MAX_EMBED_FILES:
            break
        selected.append(path)
        seen.add(path)

    if len(selected) < _MAX_EMBED_FILES:
        for path in file_tree:
            if len(selected) >= _MAX_EMBED_FILES:
                break
            if path in seen or _should_skip_file(path) or _is_skipped_path(path):
                continue
            selected.append(path)
            seen.add(path)

    return selected[:_MAX_EMBED_FILES]


def _chunk_text(text: str, max_tokens: int = _CHUNK_TOKEN_LIMIT) -> list[str]:
    """Split text into chunks not exceeding max_tokens using tiktoken."""
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
    except Exception:
        # Fallback: rough character split at ~4 chars/token
        chunk_size = max_tokens * 4
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

    chunks: list[str] = []
    for i in range(0, len(tokens), max_tokens):
        chunk_tokens = tokens[i : i + max_tokens]
        try:
            chunk_text = enc.decode(chunk_tokens)
        except Exception:
            chunk_text = text[i * 4 : (i + max_tokens) * 4]
        chunks.append(chunk_text)
    return chunks


def _keyword_match_score(path: str, subtask: Subtask) -> float:
    """
    Heuristic path relevance score based on keyword overlap with subtask text.

    Returns 0.0–1.0. Higher means more likely relevant.
    """
    combined = (subtask["title"] + " " + subtask["description"]).lower()
    words = set(re.findall(r"\w{3,}", combined))
    path_lower = path.lower()
    path_parts = set(re.findall(r"\w+", path_lower))
    overlap = words & path_parts
    if not overlap:
        return 0.0
    return min(1.0, len(overlap) / max(len(words), 1))


async def _embed_and_cache_repo(
    repo_url: str,
    file_tree: list[str],
    token: str,
    subtasks: list[Subtask],
) -> None:
    """Fetch a bounded set of text files, chunk them, embed, and store in code_embeddings."""
    github_client = get_github_client(token)
    repo = get_repo(github_client, repo_url)
    embeddings_client = _get_embeddings_client()

    chunks_to_insert: list[dict[str, Any]] = []
    text_files = _select_embed_files(file_tree, subtasks)

    logger.info(
        "[code_navigator] Embedding %d/%d files for %s (capped at %d)",
        len(text_files),
        len(file_tree),
        repo_url,
        _MAX_EMBED_FILES,
    )

    for i, path in enumerate(text_files, start=1):
        if i == 1 or i % 25 == 0 or i == len(text_files):
            logger.info("[code_navigator] Fetching file %d/%d — %s", i, len(text_files), path)
        try:
            content = get_file_content(repo, path)
            if not content or not content.strip():
                continue
            file_chunks = _chunk_text(content)
            for idx, chunk in enumerate(file_chunks):
                chunks_to_insert.append(
                    {
                        "repo_url": repo_url,
                        "file_path": path,
                        "chunk_index": idx,
                        "chunk_text": chunk,
                        "embedding": None,  # filled in batch below
                        "token_count": len(chunk) // 4,
                        "metadata": {"extension": path.rsplit(".", 1)[-1] if "." in path else ""},
                    }
                )
        except GithubException as exc:
            logger.warning("[code_navigator] Skipping %s: %s", path, exc)
            continue

    if not chunks_to_insert:
        logger.warning("[code_navigator] No embeddable chunks found for %s", repo_url)
        return

    # Batch embed in groups of 100 to avoid API payload limits
    texts = [c["chunk_text"] for c in chunks_to_insert]
    batch_size = 100
    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            vectors = await embeddings_client.aembed_documents(batch)
            all_vectors.extend(vectors)
        except Exception as exc:
            logger.error("[code_navigator] Embedding batch %d failed: %s", i // batch_size, exc)
            all_vectors.extend([[0.0] * 1536] * len(batch))

    for chunk, vector in zip(chunks_to_insert, all_vectors):
        chunk["embedding"] = vector

    await save_code_embeddings(chunks_to_insert)
    await upsert_repo_cache(repo_url, file_tree, len(chunks_to_insert))
    logger.info("[code_navigator] Stored %d embedding chunks", len(chunks_to_insert))


async def _find_files_for_subtask(
    subtask: Subtask,
    repo_url: str,
    file_tree: list[str],
    token: str,
) -> list[FileMapEntry]:
    """
    Combine pgvector semantic search with path keyword matching.

    Returns a merged, deduplicated, score-sorted list of FileMapEntry.
    """
    embeddings_client = _get_embeddings_client()
    query_text = f"{subtask['title']}\n{subtask['description']}"

    # ── Semantic search ───────────────────────────────────────────────────────
    semantic_results: dict[str, float] = {}
    try:
        query_vector = await embeddings_client.aembed_query(query_text)
        hits = await search_code_embeddings(repo_url, query_vector, limit=20)
        for hit in hits:
            path = hit["file_path"]
            score = float(hit.get("similarity", 0.0))
            if path not in semantic_results or semantic_results[path] < score:
                semantic_results[path] = score
    except Exception as exc:
        logger.warning("[code_navigator] Semantic search failed for subtask %s: %s", subtask["id"], exc)

    # ── Keyword / path matching ───────────────────────────────────────────────
    keyword_results: dict[str, float] = {}
    for path in file_tree:
        score = _keyword_match_score(path, subtask)
        if score > 0.1:
            keyword_results[path] = score

    # ── Merge ─────────────────────────────────────────────────────────────────
    all_paths: set[str] = set(semantic_results) | set(keyword_results)
    entries: list[FileMapEntry] = []
    for path in all_paths:
        sem = semantic_results.get(path, 0.0)
        kw = keyword_results.get(path, 0.0)
        if sem > 0 and kw > 0:
            source = "both"
        elif sem > 0:
            source = "pgvector"
        else:
            source = "github"
        combined_score = max(sem, kw * 0.7)  # semantic takes priority
        entries.append(FileMapEntry(path=path, relevance_score=combined_score, source=source))

    entries.sort(key=lambda e: e["relevance_score"], reverse=True)
    return entries[:_MAX_FILES_PER_SUBTASK]


async def code_navigator_node(state: PrismState, config: RunnableConfig) -> dict[str, Any]:
    """
    LangGraph node: code_navigator.

    Reads: repo_url, run_id, subtasks, repo_tree
    Config: config["configurable"]["github_token"]
    Writes: file_map, file_contents, current_agent, messages, error
    """
    run_id: str = state["run_id"]
    repo_url: str = state["repo_url"]
    github_token: str = config.get("configurable", {}).get("github_token", "")
    logger.info("[code_navigator] Starting — run_id=%s", run_id)

    try:
        await update_run_status(run_id, "running", "code_navigator")
        await save_agent_output(run_id, "code_navigator", {}, "start")

        subtasks: list[Subtask] = state.get("subtasks", [])
        file_tree: list[str] = state.get("repo_tree", [])

        if not subtasks:
            raise ValueError("No subtasks to navigate — planner may have failed")

        # ── Ensure embeddings are cached ───────────────────────────────────────
        cache = await get_repo_cache(repo_url)
        if cache is None or cache.get("embedding_count", 0) == 0:
            logger.info("[code_navigator] No embedding cache found — building now")
            await _embed_and_cache_repo(repo_url, file_tree, github_token, subtasks)
        else:
            logger.info(
                "[code_navigator] Cache hit — %d embeddings for %s",
                cache["embedding_count"],
                repo_url,
            )

        # ── Find files per subtask ─────────────────────────────────────────────
        file_map: dict[str, list[FileMapEntry]] = {}
        for subtask in subtasks:
            entries = await _find_files_for_subtask(subtask, repo_url, file_tree, github_token)
            file_map[subtask["id"]] = entries
            logger.info(
                "[code_navigator] Subtask %s → %d files", subtask["id"], len(entries)
            )

        # ── Fetch full file contents for unique paths ──────────────────────────
        unique_paths: set[str] = set()
        for entries in file_map.values():
            for entry in entries:
                unique_paths.add(entry["path"])

        github_client = get_github_client(github_token)
        repo = get_repo(github_client, repo_url)
        file_contents: dict[str, str] = {}
        for path in unique_paths:
            content = get_file_content(repo, path)
            if content:
                file_contents[path] = content

        logger.info("[code_navigator] Fetched %d file contents", len(file_contents))

        output_payload: dict[str, Any] = {
            "file_map": {k: [dict(e) for e in v] for k, v in file_map.items()},
            "file_count": len(file_contents),
        }
        await save_agent_output(run_id, "code_navigator", output_payload, "complete")

        log_line = (
            f"[code_navigator] Mapped {len(subtasks)} subtasks → "
            f"{len(unique_paths)} unique files"
        )
        logger.info(log_line)

        return {
            "file_map": file_map,
            "file_contents": file_contents,
            "current_agent": "code_navigator",
            "messages": [log_line],
        }

    except (GithubException, ValueError) as exc:
        msg = f"[code_navigator] Failed: {exc}"
        logger.error(msg, exc_info=True)
        await save_agent_output(run_id, "code_navigator", {"error": str(exc)}, "complete")
        await update_run_status(run_id, "failed", "code_navigator", error=str(exc))
        return {
            "error": str(exc),
            "current_agent": "code_navigator",
            "messages": [msg],
        }
    except Exception as exc:
        msg = f"[code_navigator] Unexpected error: {exc}"
        logger.error(msg, exc_info=True)
        await save_agent_output(run_id, "code_navigator", {"error": str(exc)}, "complete")
        await update_run_status(run_id, "failed", "code_navigator", error=str(exc))
        return {
            "error": str(exc),
            "current_agent": "code_navigator",
            "messages": [msg],
        }
