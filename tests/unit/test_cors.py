"""tests/unit/test_cors.py — CORS preflight for Vercel preview origins."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend.config import cors_allow_origin_regex, parse_frontend_origins

PREVIEW_ORIGIN = "https://prism-zbk16cgx6-sourav-manes-projects.vercel.app"


def _cors_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_frontend_origins("http://localhost:3000"),
        allow_origin_regex=cors_allow_origin_regex(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

    @app.post("/api/runs/start")
    def start() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_preflight_allows_vercel_preview_origin() -> None:
    client = TestClient(_cors_app())
    response = client.options(
        "/api/runs/start",
        headers={
            "Origin": PREVIEW_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code in (200, 204)
    assert response.headers.get("access-control-allow-origin") == PREVIEW_ORIGIN


def test_preflight_allows_localhost() -> None:
    client = TestClient(_cors_app())
    response = client.options(
        "/api/runs/start",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code in (200, 204)
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_preflight_rejects_unknown_origin() -> None:
    client = TestClient(_cors_app())
    response = client.options(
        "/api/runs/start",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.headers.get("access-control-allow-origin") != "https://evil.example.com"


def test_preflight_allows_delete() -> None:
    client = TestClient(_cors_app())
    response = client.options(
        "/api/runs/550e8400-e29b-41d4-a716-446655440000",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code in (200, 204)
    allowed = response.headers.get("access-control-allow-methods", "")
    assert "DELETE" in allowed.upper()
