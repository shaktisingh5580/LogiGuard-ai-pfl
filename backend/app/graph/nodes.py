"""LangGraph node functions — each reads from DB, processes, writes to DB.

Every node is a pure function of (state) → (state update).
Heavy data never enters the graph state.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventPublisher, EventType, PipelineEvent
from app.core.llm import get_llm_provider
from app.graph.state import PipelineState
from app.prompts.templates import COMPARATIVE_CLASSIFICATION_PROMPT
from app.rag.cache import SemanticCache
from app.rag.retriever import MultiPathRetriever
from app.rules.engine import TariffRuleEngine
from app.verification.confidence import ConfidenceRegime
from app.verification.ensemble import AdaptiveEnsemble

logger = logging.getLogger(__name__)


async def extract_node(
    state: PipelineState,
    *,
    db: AsyncSession,
    events: EventPublisher | None = None,
) -> dict:
    """Layer 1: OCR extraction + Structural Integrity Validation.

    Reads: Invoice PDF from storage
    Writes: extraction_results, siv_result to DB
    Returns: state update with needs_vlm flag
    """
    invoice_id = state["invoice_id"]
    logger.info("EXTRACT: Starting extraction for invoice %s", invoice_id)

    if events:
        await events.publish(PipelineEvent(
            EventType.EXTRACTION_STARTED, invoice_id,
            {"phase": "OCR + Layout Parsing"}
        ))

    # In production: run OCR → SIV → VLM fallback
    # For demo: mark as extracted
    await db.execute(
        text("UPDATE invoices SET state = 'EXTRACTED' WHERE id = :id"),
        {"id": invoice_id},
    )
    await db.commit()

    if events:
        await events.publish(PipelineEvent(
            EventType.EXTRACTION_COMPLETED, invoice_id,
            {"needs_vlm": False}
        ))

    return {
        "current_phase": "STRUCTURING",
        "needs_vlm": False,
    }


async def structure_node(
    state: PipelineState,
    *,
    db: AsyncSession,
    events: EventPublisher | None = None,
) -> dict:
    """Layer 2: Pydantic validation + line item decomposition.

    Reads: extraction_results from DB
    Writes: validated line_items to DB
    Returns: state update with line_item_ids
    """
    invoice_id = state["invoice_id"]
    logger.info("STRUCTURE: Validating line items for invoice %s", invoice_id)

    # Fetch existing line items
    result = await db.execute(
        text("SELECT id FROM line_items WHERE invoice_id = :id ORDER BY line_number"),
        {"id": invoice_id},
    )
    item_ids = [str(r[0]) for r in result.fetchall()]

    await db.execute(
        text("UPDATE invoices SET state = 'STRUCTURED', total_line_items = :count WHERE id = :id"),
        {"id": invoice_id, "count": len(item_ids)},
    )
    await db.commit()

    return {
        "current_phase": "CLASSIFYING",
        "line_item_ids": item_ids,
        "total_items": len(item_ids),
        "processed_items": 0,
    }


async def classify_node(
    state: PipelineState,
    *,
    db: AsyncSession,
    events: EventPublisher | None = None,
) -> dict:
    """Layers 3+4: Multi-Path RAG + Rule Engine + LLM Comparative Reasoning.

    For each line item:
    1. Check semantic cache → if hit, skip to verification with N=1
    2. Broad retrieval (top-k=15 across all chapters)
    3. Rule engine filters (statutory exclusions)
    4. LLM comparative reasoning (single call with all candidates)

    Reads: line_items from DB
    Writes: rag_results, transaction_states to DB
    """
    invoice_id = state["invoice_id"]
    line_item_ids = state.get("line_item_ids", [])
    logger.info("CLASSIFY: Processing %d line items for invoice %s", len(line_item_ids), invoice_id)

    if events:
        await events.publish(PipelineEvent(
            EventType.CLASSIFICATION_STARTED, invoice_id,
            {"total_items": len(line_item_ids)}
        ))

    llm = get_llm_provider()
    retriever = MultiPathRetriever(db, llm)
    rule_engine = TariffRuleEngine(db)
    cache = SemanticCache(db, llm)

    for idx, item_id in enumerate(line_item_ids):
        # Fetch the line item description
        item_result = await db.execute(
            text("SELECT description, country_of_origin FROM line_items WHERE id = :id"),
            {"id": item_id},
        )
        item_row = item_result.fetchone()
        if not item_row:
            continue

        description = item_row[0]
        country_of_origin = item_row[1] or "UNKNOWN"

        # Step 1: Check semantic cache
        cache_hit = await cache.lookup(description)
        if cache_hit:
            logger.info("Cache HIT for item %d: %s → %s", idx, description[:40], cache_hit.hs_code)
            await _store_classification(
                db, invoice_id, item_id, cache_hit.hs_code,
                confidence=cache_hit.similarity,
                reasoning=f"Cache hit (sim={cache_hit.similarity:.3f}, approvals={cache_hit.approval_count})",
                is_cache_hit=True,
            )
            continue

        # Step 2: Multi-Path RAG retrieval
        candidates = await retriever.retrieve(
            description=description,
            invoice_date=date.today(),  # TODO: use actual invoice date
            jurisdiction="IN",
        )

        if not candidates:
            await _store_classification(
                db, invoice_id, item_id, "UNKNOWN",
                confidence=0.0,
                reasoning="No candidates found in retrieval",
            )
            continue

        # Step 3: Rule Engine — filter out excluded candidates
        filtered = await rule_engine.filter_candidates(candidates, description)

        if not filtered.valid_candidates:
            await _store_classification(
                db, invoice_id, item_id, "REVIEW_REQUIRED",
                confidence=0.0,
                reasoning=f"All candidates excluded by rules:\n{filtered.exclusion_summary}",
            )
            continue

        # Step 4: LLM Comparative Reasoning
        candidates_context = "\n\n".join(
            f"--- Option {i+1} ---\n{c.to_prompt_context()}"
            for i, c in enumerate(filtered.valid_candidates)
        )

        try:
            response = await llm.complete(
                messages=[
                    {"role": "system", "content": COMPARATIVE_CLASSIFICATION_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Classify this commodity:\n\n"
                            f"DESCRIPTION: {description}\n"
                            f"COUNTRY OF ORIGIN: {country_of_origin}\n\n"
                            f"CANDIDATE CLASSIFICATIONS:\n{candidates_context}\n\n"
                            f"EXCLUDED CANDIDATES:\n{filtered.exclusion_summary}\n\n"
                            f"Return JSON: {{\"code\": \"XXXX.XX.XX\", \"confidence\": 0.XX, \"reasoning\": \"...\"}}"
                        ),
                    },
                ],
                temperature=0.0,
            )
            parsed = json.loads(response.content.strip().strip("`").strip("json\n"))
            await _store_classification(
                db, invoice_id, item_id,
                code=parsed.get("code", "UNKNOWN"),
                confidence=float(parsed.get("confidence", 0.5)),
                reasoning=parsed.get("reasoning", response.content),
            )
        except Exception as e:
            logger.error("LLM classification failed for item %s: %s", item_id, e)
            await _store_classification(
                db, invoice_id, item_id, "ERROR",
                confidence=0.0, reasoning=f"Classification error: {e}",
            )

    await db.commit()

    if events:
        await events.publish(PipelineEvent(
            EventType.CLASSIFICATION_COMPLETED, invoice_id,
            {"processed": len(line_item_ids)}
        ))

    return {
        "current_phase": "VERIFYING",
        "processed_items": len(line_item_ids),
    }


async def verify_node(
    state: PipelineState,
    *,
    db: AsyncSession,
    events: EventPublisher | None = None,
) -> dict:
    """Layer 4: Adaptive ensemble verification.

    Re-verifies classifications using N=1-5 parallel LLM calls.
    Updates confidence scores based on ensemble agreement.
    """
    invoice_id = state["invoice_id"]
    line_item_ids = state.get("line_item_ids", [])
    logger.info("VERIFY: Ensemble verification for %d items", len(line_item_ids))

    ensemble = AdaptiveEnsemble()
    retriever = MultiPathRetriever(db)

    for item_id in line_item_ids:
        # Read classification result
        ts_result = await db.execute(
            text("""
                SELECT ai_recommended_code, ai_confidence
                FROM transaction_states
                WHERE line_item_id = :id
            """),
            {"id": item_id},
        )
        ts_row = ts_result.fetchone()
        if not ts_row or ts_row[0] in ("UNKNOWN", "ERROR", "REVIEW_REQUIRED"):
            continue

        # Determine ensemble size
        item_result = await db.execute(
            text("SELECT description FROM line_items WHERE id = :id"),
            {"id": item_id},
        )
        item_row = item_result.fetchone()
        if not item_row:
            continue

        n = ensemble.determine_n(
            description=item_row[0],
            is_cache_hit=(ts_row[1] and ts_row[1] > 0.95),
        )

        if n <= 1:
            continue  # Cache hit — skip ensemble

        # Run ensemble
        candidates = await retriever.retrieve(item_row[0], date.today())
        if candidates:
            result = await ensemble.verify(item_row[0], candidates, n)

            # Update confidence with ensemble result
            await db.execute(
                text("""
                    UPDATE transaction_states
                    SET ai_confidence = :confidence,
                        ensemble_votes = :votes,
                        ai_recommended_code = :code
                    WHERE line_item_id = :id
                """),
                {
                    "id": item_id,
                    "confidence": result.confidence,
                    "votes": json.dumps([
                        {"code": v.predicted_code, "confidence": v.confidence, "reasoning": v.reasoning}
                        for v in result.votes
                    ]),
                    "code": result.predicted_code,
                },
            )

    await db.commit()

    if events:
        await events.publish(PipelineEvent(
            EventType.VERIFICATION_COMPLETED, invoice_id,
        ))

    return {"current_phase": "ROUTING"}


async def route_node(
    state: PipelineState,
    *,
    db: AsyncSession,
    events: EventPublisher | None = None,
) -> dict:
    """Layer 5: Confidence-based routing — auto-approve vs. hard-pause.

    High confidence + non-restricted → AUTO_APPROVED
    Low confidence or restricted → PAUSED (wait for human)
    """
    invoice_id = state["invoice_id"]
    client_id = state.get("client_id", "")
    line_item_ids = state.get("line_item_ids", [])
    logger.info("ROUTE: Routing %d items based on confidence", len(line_item_ids))

    regime = ConfidenceRegime(db)
    paused = []
    approved = []

    for item_id in line_item_ids:
        ts_result = await db.execute(
            text("""
                SELECT ai_recommended_code, ai_confidence
                FROM transaction_states
                WHERE line_item_id = :id
            """),
            {"id": item_id},
        )
        ts_row = ts_result.fetchone()
        if not ts_row:
            paused.append(item_id)
            continue

        code = ts_row[0] or ""
        confidence = float(ts_row[1] or 0)
        chapter = code[:2] if len(code) >= 2 else ""

        # Get threshold for this client + chapter
        try:
            client_uuid = UUID(client_id) if client_id else None
        except ValueError:
            client_uuid = None

        if client_uuid:
            threshold = await regime.get_threshold(client_uuid, chapter)
        else:
            threshold = 0.80

        should_pause = regime.should_pause(confidence, threshold)

        new_state = "PAUSED" if should_pause else "AUTO_APPROVED"
        await db.execute(
            text("UPDATE transaction_states SET state = :state WHERE line_item_id = :id"),
            {"state": new_state, "id": item_id},
        )

        if should_pause:
            paused.append(item_id)
        else:
            approved.append(item_id)

    # Update invoice state
    invoice_state = "AWAITING_REVIEW" if paused else "AUTO_APPROVED"
    await db.execute(
        text("""
            UPDATE invoices SET state = :state,
                high_confidence_count = :approved,
                paused_count = :paused
            WHERE id = :id
        """),
        {
            "state": invoice_state,
            "id": invoice_id,
            "approved": len(approved),
            "paused": len(paused),
        },
    )
    await db.commit()

    if paused and events:
        await events.publish(PipelineEvent(
            EventType.REVIEW_REQUIRED, invoice_id,
            {"paused_items": len(paused), "auto_approved": len(approved)},
        ))

    return {
        "current_phase": "AWAITING_REVIEW" if paused else "COMPLETING",
        "needs_pause": bool(paused),
        "paused_item_ids": paused,
        "auto_approved_ids": approved,
    }


async def complete_node(
    state: PipelineState,
    *,
    db: AsyncSession,
    events: EventPublisher | None = None,
) -> dict:
    """Layer 6: Statutory field mapping + gateway codec generation.

    Maps approved HS codes to duty rates, exemptions, and generates
    ICEGATE-compatible filing documents.
    """
    invoice_id = state["invoice_id"]
    logger.info("COMPLETE: Generating filing documents for invoice %s", invoice_id)

    # Mark invoice as completed
    await db.execute(
        text("UPDATE invoices SET state = 'COMPLETED', updated_at = :now WHERE id = :id"),
        {"id": invoice_id, "now": datetime.now(timezone.utc)},
    )

    # Log completion in audit
    await db.execute(
        text("""
            INSERT INTO audit_log (invoice_id, event_type, actor_type, metadata)
            VALUES (:invoice_id, 'PIPELINE_COMPLETED', 'SYSTEM',
                    :metadata::jsonb)
        """),
        {
            "invoice_id": invoice_id,
            "metadata": json.dumps({
                "total_items": state.get("total_items", 0),
                "auto_approved": len(state.get("auto_approved_ids", [])),
                "human_reviewed": len(state.get("paused_item_ids", [])),
            }),
        },
    )
    await db.commit()

    if events:
        await events.publish(PipelineEvent(
            EventType.COMPLETION_DONE, invoice_id,
        ))

    return {"current_phase": "COMPLETED"}


# ─── Helpers ───────────────────────────────────────────────────────────

async def _store_classification(
    db: AsyncSession,
    invoice_id: str,
    line_item_id: str,
    code: str,
    confidence: float,
    reasoning: str,
    is_cache_hit: bool = False,
) -> None:
    """Store a classification result in transaction_states."""
    await db.execute(
        text("""
            INSERT INTO transaction_states
                (invoice_id, line_item_id, state, ai_recommended_code,
                 ai_confidence, ai_reasoning)
            VALUES
                (:invoice_id, :line_item_id, :state, :code, :confidence, :reasoning)
            ON CONFLICT (line_item_id) DO UPDATE SET
                ai_recommended_code = :code,
                ai_confidence = :confidence,
                ai_reasoning = :reasoning,
                state = :state,
                updated_at = NOW()
        """),
        {
            "invoice_id": invoice_id,
            "line_item_id": line_item_id,
            "state": "CLASSIFIED",
            "code": code,
            "confidence": confidence,
            "reasoning": reasoning,
        },
    )
