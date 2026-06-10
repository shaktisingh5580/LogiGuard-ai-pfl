"""Top-level API router that mounts all sub-routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.audit import router as audit_router
from app.api.classify import router as classify_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.review import router as review_router
from app.api.upload import router as upload_router

api_router = APIRouter()

# ── Mount sub-routers ─────────────────────────────────────────
api_router.include_router(health_router, tags=["Health"])
api_router.include_router(upload_router, prefix="/api", tags=["Invoices"])
api_router.include_router(classify_router, prefix="/api", tags=["Classification"])
api_router.include_router(review_router, prefix="/api/review", tags=["Review"])
api_router.include_router(audit_router, prefix="/api", tags=["Audit"])
api_router.include_router(events_router, tags=["Events"])
