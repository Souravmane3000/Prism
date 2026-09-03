"""
backend/main.py — FastAPI application entry point.

IMPORTANT: config must be the very first import. It sets LangSmith
environment variables at module level before any LangChain import
initialises its internal tracer.
"""

# ruff: noqa: E402 — config import must come first
import backend.config  # noqa: F401 — side-effect: sets LANGCHAIN_* env vars

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import cors_allow_origin_regex, parse_frontend_origins, settings
from backend.graph import get_compiled_graph
from backend.routers.runs import router
from backend.supabase_client import ping_postgres

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Startup: verify external service connectivity and pre-compile the graph.
    Shutdown: nothing to clean up (serverless; connections are managed per-request).
    """
    logger.info("Prism starting — environment=%s", settings.environment)

    # ── Verify Postgres (session pooler) ──────────────────────────────────────
    # Do not gate startup on *.supabase.co REST. Modal regions often receive
    # Cloudflare 522 HTML from that path while the IPv4 pooler still works.
    try:
        await ping_postgres()
        logger.info("Postgres pooler verified")
    except Exception as exc:
        logger.error("Postgres pooler check failed: %s", exc, exc_info=True)
        if settings.environment == "production":
            raise

    # ── Verify OpenAI API reachability ────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            response = await http_client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
            logger.info(
                "OpenAI API reachable — status=%d model=%s",
                response.status_code,
                settings.openai_model_name,
            )
    except httpx.RequestError as exc:
        logger.warning("OpenAI API unreachable at startup: %s", exc)
        if settings.environment == "production":
            raise exc

    # ── Pre-compile the LangGraph StateGraph ──────────────────────────────────
    try:
        await get_compiled_graph()
        logger.info("LangGraph StateGraph compiled and ready")
    except Exception as exc:
        logger.error("Failed to compile LangGraph: %s", exc, exc_info=True)
        if settings.environment == "production":
            raise exc

    logger.info("Prism startup complete — ready to accept requests")
    yield
    logger.info("Prism shutting down")


app = FastAPI(
    title="Prism",
    description="Multi-agent Software Engineering Teammate",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Exact origins from FRONTEND_ORIGIN, plus a scoped regex for Vercel preview
# deployments (prism-<hash>-sourav-manes-projects.vercel.app). Never allow *.
_FRONTEND_ORIGINS = parse_frontend_origins(settings.frontend_origin)
_FRONTEND_ORIGIN_REGEX = cors_allow_origin_regex()
logger.info(
    "CORS allow_origins=%s allow_origin_regex=%s",
    _FRONTEND_ORIGINS,
    _FRONTEND_ORIGIN_REGEX,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_FRONTEND_ORIGINS,
    allow_origin_regex=_FRONTEND_ORIGIN_REGEX,
    allow_credentials=False,  # PAT is in JSON body, not cookies — no credentials needed
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(router, prefix="/api", tags=["runs"])


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Service health check — used by Modal and load balancers."""
    return {"status": "ok", "environment": settings.environment}
