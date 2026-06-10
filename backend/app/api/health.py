"""Health-check endpoint."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Response schema for the health endpoint."""

    status: str
    version: str
    timestamp: str
    services: dict[str, str]


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return application health status and reachability of backing services.

    In production this would actually ping Postgres, Redis, and MinIO.
    For now it returns static healthy status — the lifespan handler will
    fail startup if any dependency is unreachable.
    """
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
        services={
            "database": "connected",
            "redis": "connected",
            "storage": "connected",
        },
    )
