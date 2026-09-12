"""
backend/llm.py — Single cached OpenAI LLM factory.

All agents in the pipeline import get_llm() from this module.
No agent ever instantiates ChatOpenAI directly — this is a hard rule
enforced by CURSOR_RULES.md §5 and ADR-001.
"""

import asyncio
import json
import logging
import re
from functools import lru_cache
from typing import Optional, TypeVar

import httpx
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

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


MAX_TRANSIENT_ATTEMPTS = 3
MAX_VALIDATION_RETRIES = 2

TModel = TypeVar("TModel", bound=BaseModel)


class ContextOverflowError(RuntimeError):
    reason = "context_overflow"


class TransientLLMError(RuntimeError):
    reason = "llm_retry_exhausted"


class DeterministicLLMError(RuntimeError):
    reason = "llm_error"


class StructuredOutputError(RuntimeError):
    reason = "malformed_output"


def classify_llm_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "context" in text and ("length" in text or "overflow" in text):
        return "deterministic"
    if "rate" in text or "timeout" in text or "temporarily" in text or "429" in text:
        return "transient"
    return "transient"


def parse_structured(raw: str, response_model: type[TModel]) -> TModel:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"malformed JSON: {exc}") from exc
    try:
        return response_model.model_validate(data)
    except ValidationError as exc:
        raise StructuredOutputError(str(exc)) from exc


async def invoke_structured(
    messages: list[BaseMessage],
    response_model: type[TModel],
    *,
    temperature: float = 0.1,
) -> TModel:
    llm = get_llm(temperature=temperature)
    last_error: BaseException | None = None
    validation_tries = 0
    for attempt in range(1, MAX_TRANSIENT_ATTEMPTS + 1):
        try:
            response = await llm.ainvoke(messages)
            return parse_structured(str(response.content), response_model)
        except ContextOverflowError:
            raise
        except StructuredOutputError as exc:
            validation_tries += 1
            last_error = exc
            if validation_tries > MAX_VALIDATION_RETRIES:
                raise DeterministicLLMError(str(exc)) from exc
        except Exception as exc:
            last_error = exc
            kind = classify_llm_error(exc)
            if kind == "deterministic":
                if "context" in str(exc).lower():
                    raise ContextOverflowError(str(exc)) from exc
                raise DeterministicLLMError(str(exc)) from exc
            if attempt >= MAX_TRANSIENT_ATTEMPTS:
                raise TransientLLMError(str(exc)) from exc
            await asyncio.sleep(0.2 * (2 ** (attempt - 1)))
    raise TransientLLMError(str(last_error) if last_error else "LLM retry exhausted")
