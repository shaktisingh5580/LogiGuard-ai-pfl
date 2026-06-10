"""LogiGuard AI — FastAPI Backend Entry Point.

A modular, scalable backend for customs tariff classification
with a 6-layer deterministic AI pipeline.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown events."""
    settings = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("🚀 LogiGuard AI starting up (env=%s)", settings.APP_ENV)
    logger.info("   LLM Provider: %s (%s)", settings.LLM_PROVIDER, settings.LLM_MODEL)
    logger.info("   Storage: %s", settings.STORAGE_BACKEND)
    logger.info("   Database: %s", settings.DATABASE_URL.split("@")[-1])

    # Initialize database connection pool
    from app.database import engine
    logger.info("   Database engine created")

    yield

    # Shutdown
    logger.info("🛑 LogiGuard AI shutting down")
    await engine.dispose()


def create_app() -> FastAPI:
    """Application factory — creates and configures the FastAPI app."""
    settings = get_settings()

    app = FastAPI(
        title="LogiGuard AI",
        description=(
            "6-layer deterministic AI pipeline for customs tariff classification. "
            "Human-in-the-loop architecture where AI recommends, humans legally sign."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS — allow any frontend to connect
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API router
    from app.api.router import api_router
    app.include_router(api_router)

    return app


# Create the app instance
app = create_app()
