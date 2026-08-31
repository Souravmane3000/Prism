"""
backend/config.py — Environment loading and LangSmith startup configuration.

IMPORTANT: This module must be imported first in main.py before any other
LangChain/LangGraph imports. It sets LangSmith environment variables at
module-level so that auto-tracing activates for every subsequent LangGraph run.
"""

import logging
import os
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


settings = Settings()  # type: ignore[call-arg]

# ── LangSmith auto-tracing setup ─────────────────────────────────────────────
# Must happen at import time, before any LangChain import in other modules.
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"

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
    "Prism config loaded — environment=%s project=%s",
    settings.environment,
    settings.langchain_project,
)
