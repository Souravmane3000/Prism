"""
backend/config.py — Environment loading and LangSmith startup configuration.

IMPORTANT: This module must be imported first in main.py before any other
LangChain/LangGraph imports. It sets LangSmith environment variables at
module-level so that auto-tracing activates for every subsequent LangGraph run.
"""

import logging
import os
import re
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

load_dotenv()

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # ── LLM (OpenAI gpt-4o-mini + text-embedding-3-small) ─────────────────────
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model_name: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL_NAME")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL"
    )

    # ── LangSmith ────────────────────────────────────────────────────────────
    langsmith_api_key: str = Field(..., alias="LANGSMITH_API_KEY")
    langchain_project: str = Field(default="prism", alias="LANGCHAIN_PROJECT")

    # ── Supabase ─────────────────────────────────────────────────────────────
    supabase_url: str = Field(..., alias="SUPABASE_URL")
    supabase_service_key: str = Field(..., alias="SUPABASE_SERVICE_KEY")
    supabase_anon_key: str = Field(..., alias="SUPABASE_ANON_KEY")
    supabase_db_url: str = Field(..., alias="SUPABASE_DB_URL")

    # ── Application ──────────────────────────────────────────────────────────
    environment: str = Field(default="development", alias="ENVIRONMENT")
    frontend_origin: str = Field(
        default="http://localhost:5000", alias="FRONTEND_ORIGIN"
    )

    # ── Local dev only (not required in production) ──────────────────────────
    github_test_token: str = Field(default="", alias="GITHUB_TEST_TOKEN")

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "production"}
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}, got '{v}'")
        return v

    model_config = {"populate_by_name": True, "extra": "ignore"}


def normalize_psycopg_conninfo(raw: str) -> str:
    """
    Make a .env Postgres URL usable by psycopg.

    Strips SQLAlchemy's postgresql+psycopg:// prefix and percent-encodes
    the password so characters like * do not break URL parsing.
    """
    url = raw.strip().replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlparse(url)
    if not parsed.password:
        return url
    # Keep '.' unencoded — Supabase pooler usernames are postgres.<project-ref>.
    username = quote(parsed.username or "postgres", safe=".")
    password = quote(parsed.password, safe="")
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{username}:{password}@{host}{port}"
    return urlunparse(parsed._replace(netloc=netloc))


def postgres_connect_kwargs() -> dict[str, Any]:
    """
    Keyword args for psycopg / psycopg_pool.

    URI conninfo plus percent-encoding drops pooler tenant users
    (postgres.<project-ref>) and breaks passwords that contain '*'.
    """
    raw = settings.supabase_db_url.strip().replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    parsed = urlparse(raw)
    return {
        "host": parsed.hostname or "",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "dbname": (parsed.path or "/postgres").lstrip("/") or "postgres",
        "autocommit": True,
        "prepare_threshold": 0,
        "sslmode": "require",
    }


def parse_frontend_origins(raw: str) -> list[str]:
    """
    Split FRONTEND_ORIGIN into exact CORS allow-list entries.

    Comma-separated values are supported so production can allow both a
    Vercel production alias and the *.vercel.app project URL. Trailing
    slashes are stripped because browsers send Origin without one.
    """
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


# Vercel preview URLs are https://<project>-<hash>-<team>.vercel.app and change
# on every deploy. Starlette matches allow_origin_regex with fullmatch — this
# is not a wildcard for the whole internet; it is scoped to this Vercel project.
_CORS_ORIGIN_REGEX = (
    r"https://prism(-[a-z0-9]+)*-sourav-manes-projects\.vercel\.app"
    r"|https://prism-beta-one\.vercel\.app"
    r"|http://(localhost|127\.0\.0\.1)(:\d+)?"
)


def cors_allow_origin_regex() -> str:
    """Regex passed to CORSMiddleware.allow_origin_regex."""
    return _CORS_ORIGIN_REGEX


def origin_allowed_by_cors(origin: str, allowed_origins: list[str]) -> bool:
    """True if Origin is an exact allow-list entry or matches the preview regex."""
    normalized = origin.strip().rstrip("/")
    if normalized in allowed_origins:
        return True
    return re.fullmatch(_CORS_ORIGIN_REGEX, normalized) is not None


settings = Settings()  # type: ignore[call-arg]

# LangSmith project names are case-sensitive. The live workspace is "Prism".
# Modal secret *name* "prism-secrets" is unrelated and must not be renamed.
LANGSMITH_UI_PROJECT = "Prism"


def _strip_env_quotes(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _canonical_langsmith_project(raw: str) -> str:
    """Map prism/Prism/"Prism" onto the LangSmith UI project name."""
    name = _strip_env_quotes(raw)
    if not name or name.lower() == "prism":
        return LANGSMITH_UI_PROJECT
    return name


def configure_langsmith_tracing() -> str:
    """
    Enable LangSmith for this process and return the project name.

    Sets both LANGSMITH_* (current SDK) and LANGCHAIN_* (legacy) names.
    Clears langsmith's lru_cached env lookups so a late call still wins
    if LangGraph was imported before this module.

    Prefer LANGSMITH_PROJECT (the LangSmith UI name) over LANGCHAIN_PROJECT.
    """
    project = _canonical_langsmith_project(
        os.environ.get("LANGSMITH_PROJECT")
        or os.environ.get("LANGCHAIN_PROJECT")
        or settings.langchain_project
        or LANGSMITH_UI_PROJECT
    )

    endpoint = _strip_env_quotes(
        os.environ.get("LANGSMITH_ENDPOINT")
        or os.environ.get("LANGCHAIN_ENDPOINT")
        or "https://api.smith.langchain.com"
    )

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_CALLBACKS_BACKGROUND"] = "false"
    os.environ["LANGSMITH_TRACING_BACKGROUND"] = "false"
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = project
    os.environ["LANGSMITH_PROJECT"] = project
    os.environ["LANGCHAIN_ENDPOINT"] = endpoint
    os.environ["LANGSMITH_ENDPOINT"] = endpoint

    try:
        from langsmith.utils import get_env_var, get_tracer_project

        get_env_var.cache_clear()
        get_tracer_project.cache_clear()
    except Exception:
        logger.debug("Could not clear langsmith env cache", exc_info=True)

    return project


def flush_langsmith_traces() -> None:
    """Block until queued traces are posted. Required on Modal before the worker exits."""
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    except Exception:
        logger.debug("wait_for_all_tracers failed", exc_info=True)


# ── LangSmith auto-tracing setup ─────────────────────────────────────────────
# Must happen at import time, before any LangChain import in other modules.
_langsmith_project = configure_langsmith_tracing()

# ── Logging configuration ─────────────────────────────────────────────────────
_log_level = logging.DEBUG if settings.environment == "development" else logging.INFO
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
# httpx/hpack DEBUG encodes request headers, including the Supabase service-role JWT.
for _noisy_logger in (
    "hpack",
    "hpack.hpack",
    "hpack.table",
    "httpcore",
    "httpcore.http2",
    "httpcore.http11",
    "httpcore.connection",
    "httpx",
):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

logger.info(
    "Prism config loaded — environment=%s langsmith_project=%s tracing=on "
    "(Modal secret name prism-secrets is not the LangSmith project)",
    settings.environment,
    _langsmith_project,
)
