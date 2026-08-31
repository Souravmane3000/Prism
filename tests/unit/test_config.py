"""tests/unit/test_config.py — Tests for backend/config.py"""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError


class TestSettings:
    def test_loads_all_required_fields(self):
        """Settings object loads all required fields from env vars set in conftest."""
        from backend.config import settings

        assert settings.openai_api_key
        assert settings.openai_model_name
        assert settings.supabase_url
        assert settings.supabase_service_key
        assert settings.supabase_db_url

    def test_default_frontend_origin(self):
        """Default FRONTEND_ORIGIN is http://localhost:5000."""
        from backend.config import settings

        assert settings.frontend_origin == "http://localhost:5000"

    def test_default_environment_is_development(self):
        """Default ENVIRONMENT is 'development'."""
        from backend.config import settings

        assert settings.environment == "development"

    def test_langsmith_env_vars_set_at_import(self):
        """LangSmith environment variables are injected into os.environ after import."""
        assert os.environ.get("LANGCHAIN_TRACING_V2") == "true"
        assert os.environ.get("LANGCHAIN_PROJECT") == "prism"
        assert os.environ.get("LANGCHAIN_ENDPOINT") == "https://api.smith.langchain.com"
        assert "LANGCHAIN_API_KEY" in os.environ

    def test_environment_validator_rejects_invalid_value(self):
        """ENVIRONMENT validator raises ValidationError for invalid values."""
        from pydantic_settings import BaseSettings
        from pydantic import Field, field_validator

        class _TestSettings(BaseSettings):
            environment: str = Field(default="development", alias="ENVIRONMENT")
            openai_api_key: str = Field(default="x", alias="OPENAI_API_KEY")
            openai_model_name: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL_NAME")
            openai_embedding_model: str = Field(
                default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL"
            )
            langsmith_api_key: str = Field(default="x", alias="LANGSMITH_API_KEY")
            supabase_url: str = Field(default="x", alias="SUPABASE_URL")
            supabase_service_key: str = Field(default="x", alias="SUPABASE_SERVICE_KEY")
            supabase_anon_key: str = Field(default="x", alias="SUPABASE_ANON_KEY")
            supabase_db_url: str = Field(default="x", alias="SUPABASE_DB_URL")
            langchain_project: str = Field(default="prism", alias="LANGCHAIN_PROJECT")
            frontend_origin: str = Field(default="http://localhost:5000", alias="FRONTEND_ORIGIN")
            github_test_token: str = Field(default="", alias="GITHUB_TEST_TOKEN")

            @field_validator("environment")
            @classmethod
            def validate_environment(cls, v: str) -> str:
                allowed = {"development", "production"}
                if v not in allowed:
                    raise ValueError(f"ENVIRONMENT must be one of {allowed}, got '{v}'")
                return v

            model_config = {"populate_by_name": True, "extra": "ignore"}

        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
            with pytest.raises(ValidationError):
                _TestSettings()

    def test_normalize_psycopg_conninfo_encodes_password_and_strips_prefix(self):
        from backend.config import normalize_psycopg_conninfo

        raw = "postgresql+psycopg://postgres:Supabase123*@db.example.supabase.co:5432/postgres"
        out = normalize_psycopg_conninfo(raw)
        assert out.startswith("postgresql://")
        assert "postgresql+psycopg" not in out
        assert "Supabase123%2A" in out
        assert "db.example.supabase.co:5432" in out

    def test_normalize_psycopg_conninfo_keeps_pooler_username_dots(self):
        """Supabase session pooler user is postgres.<project-ref>; encoding '.' breaks auth."""
        from backend.config import normalize_psycopg_conninfo

        raw = (
            "postgresql://postgres.myref:Supabase123*"
            "@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres"
        )
        out = normalize_psycopg_conninfo(raw)
        assert "postgres.myref:" in out
        assert "postgres%2E" not in out
        assert "Supabase123%2A" in out

    def test_parse_frontend_origins_splits_and_strips(self):
        from backend.config import parse_frontend_origins

        assert parse_frontend_origins(
            "https://prism-beta-one.vercel.app/, https://prism-sourav-manes-projects.vercel.app"
        ) == [
            "https://prism-beta-one.vercel.app",
            "https://prism-sourav-manes-projects.vercel.app",
        ]

    def test_vercel_preview_origin_is_allowed_by_cors_regex(self):
        """Each Vercel preview deployment gets a unique host; CORS must still allow it."""
        from backend.config import origin_allowed_by_cors, parse_frontend_origins

        allowed = parse_frontend_origins("http://localhost:3000")
        preview = "https://prism-zbk16cgx6-sourav-manes-projects.vercel.app"
        assert origin_allowed_by_cors(preview, allowed)
        assert origin_allowed_by_cors("https://prism-beta-one.vercel.app", allowed)
        assert origin_allowed_by_cors("https://prism-sourav-manes-projects.vercel.app", allowed)
        assert origin_allowed_by_cors("http://localhost:3000", allowed)
        assert origin_allowed_by_cors("http://127.0.0.1:8000", allowed)
        assert not origin_allowed_by_cors("https://evil.example.com", allowed)

    def test_langchain_project_defaults_to_prism(self):
        """LANGCHAIN_PROJECT defaults to 'prism'."""
        from backend.config import settings

        assert settings.langchain_project == "prism"
