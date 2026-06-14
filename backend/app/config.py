"""Application configuration via Pydantic Settings.

Loads values from environment variables and/or a `.env` file located at
the project root.  Every setting has a sensible local-development default
so the app can boot with zero configuration on a dev machine that has
docker-compose running.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to *this* file → backend/app/config.py → backend/.env
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    """Central configuration object – one instance per process."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────
    DATABASE_URL: str = (
        "postgresql+asyncpg://logiguard:logiguard_dev@localhost:5435/logiguard"
    )

    # ── Redis ─────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Object Storage (S3 / MinIO) ──────────────────────────
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET_NAME: str = "logiguard-documents"
    S3_REGION: str = "us-east-1"

    # ── OpenAI / LLM ─────────────────────────────────────────
    OPENAI_API_KEY: str = "sk-change-me"
    OPENAI_BASE_URL: str | None = None
    LLM_MODEL: str = "gpt-4o"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536

    # ── Google Gemini (via google-genai SDK) ──────────────────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_EMBEDDING_MODEL: str = "embedding-001"

    # ── Application ───────────────────────────────────────────
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: List[str] = ["*"]

    # ── Pluggable Backends ────────────────────────────────────
    LLM_PROVIDER: str = "openai"              # openai | gemini
    STORAGE_BACKEND: str = "local"            # local | s3
    LOCAL_STORAGE_PATH: str = "./storage"     # For local storage backend

    # ── Aliases for core modules ──────────────────────────────
    @property
    def openai_api_key(self) -> str:
        return self.OPENAI_API_KEY

    @property
    def openai_base_url(self) -> str | None:
        return self.OPENAI_BASE_URL

    @property
    def llm_model(self) -> str:
        return self.LLM_MODEL

    @property
    def llm_provider(self) -> str:
        return self.LLM_PROVIDER

    @property
    def embedding_model(self) -> str:
        return self.EMBEDDING_MODEL

    @property
    def gemini_api_key(self) -> str:
        return self.GEMINI_API_KEY

    @property
    def gemini_model(self) -> str:
        return self.GEMINI_MODEL

    @property
    def gemini_embedding_model(self) -> str:
        return self.GEMINI_EMBEDDING_MODEL

    @property
    def s3_endpoint_url(self) -> str:
        return self.S3_ENDPOINT_URL

    @property
    def s3_access_key(self) -> str:
        return self.S3_ACCESS_KEY

    @property
    def s3_secret_key(self) -> str:
        return self.S3_SECRET_KEY

    @property
    def s3_region(self) -> str:
        return self.S3_REGION

    @property
    def s3_bucket(self) -> str:
        return self.S3_BUCKET_NAME

    @property
    def storage_backend(self) -> str:
        return self.STORAGE_BACKEND

    @property
    def local_storage_path(self) -> str:
        return self.LOCAL_STORAGE_PATH

    TESSERACT_CMD: str = "tesseract"
    POPPLER_PATH: str = ""

    # ── Validators ────────────────────────────────────────────
    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        """Accept a JSON-encoded string or a plain list."""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v  # type: ignore[return-value]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()

