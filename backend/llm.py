"""
backend/llm.py — Single cached OpenAI LLM factory.

All agents in the pipeline import get_llm() from this module.
No agent ever instantiates ChatOpenAI directly — this is a hard rule
enforced by CURSOR_RULES.md §5 and ADR-001.
"""

import logging
from functools import lru_cache
from typing import Optional

import httpx
from langchain_openai import ChatOpenAI

from backend.config import settings

logger = logging.getLogger(__name__)

_http_client: Optional[httpx.Client] = None
_http_async_client: Optional[httpx.AsyncClient] = None


def _openai_http_clients() -> tuple[httpx.Client, httpx.AsyncClient]:
    """Shared HTTP/1.1 clients — HTTP/2 to Cloudflare is a known COMPRESSION_ERROR source."""
    global _http_client, _http_async_client
    if _http_client is None or _http_async_client is None:
        timeout = httpx.Timeout(120.0, connect=15.0)
        _http_client = httpx.Client(http2=False, timeout=timeout)
        _http_async_client = httpx.AsyncClient(http2=False, timeout=timeout)
    return _http_client, _http_async_client


@lru_cache(maxsize=8)
def get_llm(temperature: float = 0.1) -> ChatOpenAI:
    """
    Return the cached OpenAI ChatOpenAI client.

    The @lru_cache ensures exactly one instance is ever created per
    temperature value. Call with the default temperature for all
    generation tasks; pass a lower value for more deterministic outputs
    (e.g. structured JSON extraction).
    """
    logger.info(
        "Initialising OpenAI LLM client — model=%s temperature=%s",
        settings.openai_model_name,
        temperature,
    )
    sync_http, async_http = _openai_http_clients()
    return ChatOpenAI(
        api_key=settings.openai_api_key,
        model=settings.openai_model_name,
        temperature=temperature,
        streaming=False,
        disable_streaming=True,
        max_retries=3,
        timeout=120,
        http_client=sync_http,
        http_async_client=async_http,
    )
