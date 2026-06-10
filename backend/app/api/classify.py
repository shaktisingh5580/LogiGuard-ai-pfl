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
    from app.core.llm import get_llm_provider
    from app.rag.retriever import MultiPathRetriever
    from app.rules.engine import TariffRuleEngine
    from app.prompts.templates import COMPARATIVE_CLASSIFICATION_PROMPT
    import json as _json

    llm = get_llm_provider()
    retriever = MultiPathRetriever(session, llm)
    rule_engine = TariffRuleEngine(session)

    # Step 1: Multi-Path RAG retrieval
    from datetime import date
    candidates_raw = await retriever.retrieve(
        description=body.description,
        invoice_date=date.today(),
        jurisdiction=body.country_of_origin or "IN",
    )

    response_candidates: list[CandidatePath] = []
    excluded_list: list[ExcludedCandidate] = []
    final_code = "0000.00.0000"
    final_confidence = 0.0
    final_reasoning = "No candidates found"
    needs_review = True
    review_reason = "No classification candidates found in tariff database"

    if candidates_raw:
        # Step 2: Rule Engine filter
        filtered = await rule_engine.filter_candidates(candidates_raw, body.description)

        # Build excluded list
        for exc in filtered.excluded_candidates:
            excluded_list.append(ExcludedCandidate(
                hs_code=exc.code,
                description=exc.description,
                exclusion_reason=exc.exclusion_reason,
                confidence=0.0,
            ))

        if filtered.valid_candidates:
            # Step 3: LLM Comparative Reasoning
            candidates_context = "\n\n".join(
                f"--- Option {i+1} ---\n"
                f"Code: {c.code}\n"
                f"Description: {c.description}\n"
                f"Section Notes: {c.section_notes or 'N/A'}\n"
                f"Chapter Notes: {c.chapter_notes or 'N/A'}\n"
                f"Similarity Score: {c.similarity_score:.3f}"
                for i, c in enumerate(filtered.valid_candidates)
            )

            try:
                llm_response = await llm.complete(
                    messages=[
                        {"role": "system", "content": COMPARATIVE_CLASSIFICATION_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"Classify this commodity:\n\n"
                                f"DESCRIPTION: {body.description}\n"
                                f"COUNTRY OF ORIGIN: {body.country_of_origin or 'Not specified'}\n\n"
                                f"CANDIDATE CLASSIFICATIONS:\n{candidates_context}\n\n"
                                f"Return JSON: {{\"code\": \"XXXX.XX.XX\", \"confidence\": 0.XX, \"reasoning\": \"...\"}}"
                            ),
                        },
                    ],
                    temperature=0.0,
                )

                # Parse LLM JSON response
                raw_content = llm_response.content.strip()
                # Strip markdown code fences if present
                if raw_content.startswith("```"):
                    raw_content = raw_content.split("\n", 1)[-1].rsplit("```", 1)[0]
                parsed = _json.loads(raw_content)

                final_code = parsed.get("code", "0000.00.0000")
                final_confidence = float(parsed.get("confidence", 0.5))
                final_reasoning = parsed.get("reasoning", raw_content)
                needs_review = final_confidence < 0.85
                review_reason = None if not needs_review else (
                    f"Confidence {final_confidence:.0%} below auto-approve threshold (85%)"
                )

                # Build candidate list for response
                response_candidates.append(CandidatePath(
                    hs_code=final_code,
                    description=final_reasoning[:200],
                    confidence=final_confidence,
                    strategy="ensemble_llm",
                    reasoning=final_reasoning,
                ))

            except Exception as llm_err:
                final_code = "REVIEW_REQUIRED"
                final_confidence = 0.0
                needs_review = True
                review_reason = f"LLM classification error: {llm_err}"

    # Persist the TransactionState
    txn = TransactionState(
        status="completed" if not needs_review else "pending_review",
        current_node="classification_complete",
        final_hs_code=final_code,
        final_confidence=final_confidence,
        needs_review=needs_review,
        review_reason=review_reason,
    )
    session.add(txn)
    await session.flush()
    await session.refresh(txn)

    # Write back to cache for future lookups
    if final_confidence >= 0.85:
        new_cache = ClassificationCache(
            description_hash=desc_hash,
            description_text=body.description[:500],
            hs_code=final_code,
            confidence=final_confidence,
            source_strategy="ensemble_llm",
        )
        session.add(new_cache)

    elapsed = (time.monotonic_ns() // 1_000_000) - start_ms

    return ClassifyResponse(
        transaction_id=txn.id,
        description=body.description,
        recommended_hs_code=final_code,
        confidence=final_confidence,
        needs_review=needs_review,
        review_reason=review_reason,
        candidates=response_candidates,
        excluded=excluded_list,
        processing_time_ms=elapsed,
        cache_hit=cache_hit,
        created_at=datetime.now(timezone.utc),
    )
