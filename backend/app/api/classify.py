"""Direct classification endpoint.

Accepts a product description and optional context, runs the classification
pipeline, and returns the result.  This is the "classify without uploading
an invoice" path.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.cache import ClassificationCache
from app.models.classification import TransactionState
from app.schemas.classification import (
    CandidatePath,
    ClassifyRequest,
    ClassifyResponse,
    ExcludedCandidate,
)

router = APIRouter()


def _hash_description(text: str) -> str:
    """Produce a deterministic SHA-256 hash for cache lookups."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


@router.post("/classify", response_model=ClassifyResponse, status_code=200)
async def classify_description(
    body: ClassifyRequest,
    session: AsyncSession = Depends(get_session),
) -> ClassifyResponse:
    """Classify a product description and return HS tariff code candidates.

    The implementation follows this flow:
    1. Check the classification cache (unless ``force_reclassify`` is set).
    2. Run the full ensemble classification pipeline.
    3. Persist the TransactionState and return the result.

    The actual LLM + RAG orchestration will be wired in by the pipeline
    layer; this endpoint handles request validation, caching, and persistence.
    """
    start_ms = time.monotonic_ns() // 1_000_000

    # ── 1. Cache lookup ───────────────────────────────────────
    desc_hash = _hash_description(body.description)
    cache_hit = False

    if not body.force_reclassify:
        stmt = select(ClassificationCache).where(
            ClassificationCache.description_hash == desc_hash,
        )
        cached = (await session.execute(stmt)).scalar_one_or_none()

        if cached is not None:
            cache_hit = True
            # Update hit counter
            cached.hit_count += 1
            cached.last_hit_at = datetime.now(timezone.utc)

            # Create a lightweight transaction state for auditing
            txn = TransactionState(
                status="completed",
                current_node="cache_hit",
                final_hs_code=cached.hs_code,
                final_confidence=float(cached.confidence),
                needs_review=False,
            )
            session.add(txn)
            await session.flush()

            elapsed = (time.monotonic_ns() // 1_000_000) - start_ms
            return ClassifyResponse(
                transaction_id=txn.id,
                description=body.description,
                recommended_hs_code=cached.hs_code,
                confidence=float(cached.confidence),
                needs_review=False,
                candidates=[
                    CandidatePath(
                        hs_code=cached.hs_code,
                        description=cached.description_text,
                        confidence=float(cached.confidence),
                        strategy="cache",
                        reasoning="Returned from classification cache",
                    ),
                ],
                excluded=[],
                processing_time_ms=elapsed,
                cache_hit=True,
                created_at=datetime.now(timezone.utc),
            )

    # ── 2. Full classification pipeline ──────────────────────
    # The real LLM ensemble + RAG pipeline will be injected here.
    # For now we create a pending transaction state so the API contract
    # is fully exercised end-to-end.
    txn = TransactionState(
        status="pending",
        current_node="classification_start",
        needs_review=True,
        review_reason="Pipeline not yet wired — manual review required",
    )
    session.add(txn)
    await session.flush()
    await session.refresh(txn)

    elapsed = (time.monotonic_ns() // 1_000_000) - start_ms

    # Placeholder response — will be replaced once the LangGraph pipeline
    # is integrated.
    return ClassifyResponse(
        transaction_id=txn.id,
        description=body.description,
        recommended_hs_code="0000.00.0000",
        confidence=0.0,
        needs_review=True,
        review_reason="Classification pipeline pending integration",
        candidates=[],
        excluded=[],
        processing_time_ms=elapsed,
        cache_hit=cache_hit,
        created_at=datetime.now(timezone.utc),
    )
