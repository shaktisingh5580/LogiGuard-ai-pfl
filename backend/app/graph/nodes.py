"""LangGraph node functions — each reads from DB, processes, writes to DB.

Every node is a pure function of (state) → (state update).
Heavy data never enters the graph state.

IMPORTANT: All raw SQL must reference ACTUAL columns from the ORM models:
  - Invoice: id, client_id, filename, storage_key, status, raw_text, metadata, updated_at
  - TransactionState: id, invoice_id, line_item_id, status, current_node,
                      final_hs_code, final_confidence, needs_review, review_reason,
                      graph_state, started_at, completed_at
  - LineItem: id, invoice_id, line_number, description, quantity, unit,
              unit_price, total_price, currency, country_of_origin, hs_code, confidence
  - AuditLog: id, invoice_id, client_id, action, actor, entity_type, entity_id,
              before_state, after_state, details
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
    """Layer 1: PDF text extraction + Structural Integrity Validation.

    Reads the PDF from storage, extracts text using pymupdf4llm (or PyMuPDF
    fallback), runs SIV checks, and stores the extracted text on the invoice.

    Reads: Invoice record + stored PDF bytes
    Writes: invoice.raw_text, invoice.status
    Returns: state update with needs_vlm flag
    """
    invoice_id = state["invoice_id"]
    logger.info("EXTRACT: Starting extraction for invoice %s", invoice_id)

    if events:
        await events.publish(PipelineEvent(
            EventType.EXTRACTION_STARTED, invoice_id,
            {"phase": "PDF → Markdown extraction"}
        ))

    # Fetch the invoice to get the storage key
    result = await db.execute(
        text("SELECT storage_key, status FROM invoices WHERE id = :id"),
        {"id": invoice_id},
    )
    inv_row = result.fetchone()
    if not inv_row:
        logger.error("Invoice %s not found in database", invoice_id)
        return {"current_phase": "ERROR", "error_state": "InvoiceNotFound"}

    storage_key = inv_row[0]
    needs_vlm = False

    # Attempt to read the PDF file and extract text
    try:
        from app.pipeline.processor import extract_text_from_pdf_bytes
        from app.core.storage import get_storage_backend

        storage = get_storage_backend()
        pdf_bytes = await storage.download(storage_key)
        extracted_text = extract_text_from_pdf_bytes(pdf_bytes)

        # Store the extracted text on the invoice
        await db.execute(
            text("""
                UPDATE invoices
                SET raw_text = :text, status = 'extracted', updated_at = :now
                WHERE id = :id
            """),
            {
                "text": extracted_text,
                "id": invoice_id,
                "now": datetime.now(timezone.utc),
            },
        )

        # Run Structural Integrity Validation if possible
        try:
            from app.extraction.ocr import RawExtraction
            from app.extraction.siv import StructuralIntegrityValidator

            raw = RawExtraction(
                text=extracted_text,
                bounding_boxes=[],
                confidence=90.0,
                page_images=[],
                page_count=1,
                source_path=storage_key,
                is_mock=False,
            )
            siv = StructuralIntegrityValidator()
            siv_result = siv.validate(raw)
            needs_vlm = siv_result.force_vlm

            if siv_result.violations:
                logger.warning(
                    "SIV found %d violations (force_vlm=%s)",
                    len(siv_result.violations), needs_vlm,
                )
        except Exception as siv_err:
            logger.warning("SIV validation skipped: %s", siv_err)

    except Exception as e:
        logger.error("Extraction failed for invoice %s: %s", invoice_id, e)
        await db.execute(
            text("""
                UPDATE invoices
                SET status = 'extraction_failed', updated_at = :now
                WHERE id = :id
            """),
            {"id": invoice_id, "now": datetime.now(timezone.utc)},
        )
        await db.commit()
        return {
            "current_phase": "ERROR",
            "error_state": "ExtractionFailed",
            "error_detail": str(e),
        }

    await db.commit()

    if events:
        await events.publish(PipelineEvent(
            EventType.EXTRACTION_COMPLETED, invoice_id,
            {"needs_vlm": needs_vlm, "text_length": len(extracted_text)}
        ))

    return {
        "current_phase": "STRUCTURING",
        "needs_vlm": needs_vlm,
    }


async def structure_node(
    state: PipelineState,
    *,
    db: AsyncSession,
    events: EventPublisher | None = None,
) -> dict:
    """Layer 2: Pydantic validation + line item decomposition.

    Reads: line_items already created during extraction (by InvoiceProcessor)
           or creates them from raw_text if they don't exist yet.
    Writes: validated line_items to DB
    Returns: state update with line_item_ids
    """
    invoice_id = state["invoice_id"]
    logger.info("STRUCTURE: Validating line items for invoice %s", invoice_id)

    # Fetch existing line items (may have been created by InvoiceProcessor)
    result = await db.execute(
        text("SELECT id FROM line_items WHERE invoice_id = :id ORDER BY line_number"),
        {"id": invoice_id},
    )
    item_ids = [str(r[0]) for r in result.fetchall()]

    # Update invoice status (using actual ORM column: 'status', not 'state')
    await db.execute(
        text("UPDATE invoices SET status = 'structured', updated_at = :now WHERE id = :id"),
        {"id": invoice_id, "now": datetime.now(timezone.utc)},
    )
    await db.commit()

    if events:
        await events.publish(PipelineEvent(
            EventType.EXTRACTION_COMPLETED, invoice_id,
            {"total_items": len(item_ids), "phase": "structuring_complete"}
        ))

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
    Writes: transaction_states to DB
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

    # Semantic cache — wrap in try-except since it needs DB tables that may not exist
    cache = None
    try:
        from app.rag.cache import SemanticCache
        cache = SemanticCache(db, llm)
    except Exception as e:
        logger.warning("SemanticCache unavailable: %s", e)

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

        # Step 1: Check semantic cache (if available)
        if cache:
            try:
                cache_hit = await cache.lookup(description)
                if cache_hit:
                    logger.info("Cache HIT for item %d: %s → %s", idx, description[:40], cache_hit.hs_code)
                    await _store_classification(
                        db, invoice_id, item_id, cache_hit.hs_code,
                        confidence=cache_hit.similarity,
                        reasoning=f"Cache hit (sim={cache_hit.similarity:.3f}, approvals={cache_hit.approval_count})",
                    )
                    continue
            except Exception as e:
                logger.warning("Cache lookup failed for item %d: %s", idx, e)

        # Step 2: Multi-Path RAG retrieval
        try:
            candidates = await retriever.retrieve(
                description=description,
                invoice_date=date.today(),
                jurisdiction="IN",
            )
        except Exception as e:
            logger.warning("RAG retrieval failed for item %d: %s", idx, e)
            candidates = []

        if not candidates:
            await _store_classification(
                db, invoice_id, item_id, "UNKNOWN",
                confidence=0.0,
                reasoning="No candidates found in retrieval",
            )
            continue

        # Step 3: Rule Engine — filter out excluded candidates
        try:
            filtered = await rule_engine.filter_candidates(candidates, description)
        except Exception as e:
            logger.warning("Rule engine failed for item %d: %s", idx, e)
            # Proceed with unfiltered candidates
            class _FallbackFiltered:
                valid_candidates = candidates
                excluded_candidates = []
                exclusion_summary = ""
            filtered = _FallbackFiltered()

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
            # Parse JSON, stripping markdown code fences
            raw_content = response.content.strip()
            if raw_content.startswith("```"):
                raw_content = raw_content.split("\n", 1)[-1].rsplit("```", 1)[0]
            parsed = json.loads(raw_content)

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

        # Publish progress
        if events and (idx + 1) % 5 == 0:
            await events.publish(PipelineEvent(
                EventType.CLASSIFICATION_STARTED, invoice_id,
                {"processed": idx + 1, "total": len(line_item_ids)}
            ))

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

    llm = get_llm_provider()
    ensemble = AdaptiveEnsemble(llm)

    for item_id in line_item_ids:
        # Read classification result using actual column names
        ts_result = await db.execute(
            text("""
                SELECT final_hs_code, final_confidence
                FROM transaction_states
                WHERE line_item_id = :id
                ORDER BY started_at DESC
                LIMIT 1
            """),
            {"id": item_id},
        )
        ts_row = ts_result.fetchone()
        if not ts_row or ts_row[0] in ("UNKNOWN", "ERROR", "REVIEW_REQUIRED"):
            continue

        current_code = ts_row[0]
        current_confidence = float(ts_row[1] or 0)

        # Fetch item description for ensemble
        item_result = await db.execute(
            text("SELECT description FROM line_items WHERE id = :id"),
            {"id": item_id},
        )
        item_row = item_result.fetchone()
        if not item_row:
            continue

        description = item_row[0]

        # Determine ensemble size
        n = ensemble.determine_n(
            description=description,
            is_cache_hit=(current_confidence > 0.95),
        )

        if n <= 1:
            continue  # Cache hit or very high confidence — skip ensemble

        # Run ensemble verification with RAG candidates
        try:
            retriever = MultiPathRetriever(db, llm)
            candidates = await retriever.retrieve(description, date.today())
            if candidates:
                result = await ensemble.verify(description, candidates, n)

                # Update the TransactionState with ensemble results
                # Store votes in graph_state JSONB column (not a separate column)
                votes_data = [
                    {"code": v.predicted_code, "confidence": v.confidence, "reasoning": v.reasoning}
                    for v in result.votes
                ]
                await db.execute(
                    text("""
                        UPDATE transaction_states
                        SET final_confidence = :confidence,
                            final_hs_code = :code,
                            graph_state = :graph_state,
                            current_node = 'ensemble_verified'
                        WHERE line_item_id = :id
                          AND id = (
                              SELECT id FROM transaction_states
                              WHERE line_item_id = :id
                              ORDER BY started_at DESC LIMIT 1
                          )
                    """),
                    {
                        "id": item_id,
                        "confidence": result.confidence,
                        "code": result.predicted_code,
                        "graph_state": json.dumps({
                            "ensemble_votes": votes_data,
                            "agreement_ratio": result.agreement_ratio,
                            "ensemble_size": result.ensemble_size,
                        }),
                    },
                )
        except Exception as e:
            logger.warning("Ensemble verification failed for item %s: %s", item_id, e)

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
    paused: list[str] = []
    approved: list[str] = []

    for item_id in line_item_ids:
        # Read using actual TransactionState columns
        ts_result = await db.execute(
            text("""
                SELECT id, final_hs_code, final_confidence
                FROM transaction_states
                WHERE line_item_id = :id
                ORDER BY started_at DESC
                LIMIT 1
            """),
            {"id": item_id},
        )
        ts_row = ts_result.fetchone()
        if not ts_row:
            paused.append(item_id)
            continue

        txn_id = ts_row[0]
        code = ts_row[1] or ""
        confidence = float(ts_row[2] or 0)
        chapter = code[:2] if len(code) >= 2 else ""

        # Get threshold for this client + chapter
        try:
            client_uuid = UUID(client_id) if client_id else None
        except (ValueError, TypeError):
            client_uuid = None

        if client_uuid:
            try:
                threshold = await regime.get_threshold(client_uuid, chapter)
            except Exception:
                threshold = 0.80
        else:
            threshold = 0.80

        should_pause = regime.should_pause(confidence, threshold)
        new_status = "pending_review" if should_pause else "completed"

        # Update TransactionState with actual column names
        await db.execute(
            text("""
                UPDATE transaction_states
                SET status = :status,
                    needs_review = :needs_review,
                    review_reason = :reason,
                    current_node = 'route'
                WHERE id = :id
            """),
            {
                "status": new_status,
                "needs_review": should_pause,
                "reason": (
                    f"Confidence {confidence:.0%} below threshold {threshold:.0%}"
                    if should_pause else None
                ),
                "id": txn_id,
            },
        )

        # Also update the line_item with the classification
        await db.execute(
            text("""
                UPDATE line_items
                SET hs_code = :code, confidence = :confidence
                WHERE id = :item_id
            """),
            {"code": code, "confidence": confidence, "item_id": item_id},
        )

        if should_pause:
            paused.append(item_id)
        else:
            approved.append(item_id)

    # Update invoice status (using actual column: 'status')
    invoice_status = "pending_review" if paused else "classified"
    await db.execute(
        text("""
            UPDATE invoices
            SET status = :status, updated_at = :now
            WHERE id = :id
        """),
        {
            "status": invoice_status,
            "id": invoice_id,
            "now": datetime.now(timezone.utc),
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

    # Mark invoice as completed (using actual column: 'status')
    await db.execute(
        text("UPDATE invoices SET status = 'completed', updated_at = :now WHERE id = :id"),
        {"id": invoice_id, "now": datetime.now(timezone.utc)},
    )

    # Complete all remaining transaction_states for this invoice
    await db.execute(
        text("""
            UPDATE transaction_states
            SET status = 'completed',
                completed_at = :now,
                current_node = 'complete'
            WHERE invoice_id = :id
              AND status NOT IN ('completed', 'modified', 'rejected')
        """),
        {"id": invoice_id, "now": datetime.now(timezone.utc)},
    )

    # Log completion in audit trail (using actual AuditLog column names)
    try:
        await db.execute(
            text("""
                INSERT INTO audit_logs
                    (invoice_id, action, actor, entity_type, entity_id, after_state, details)
                VALUES
                    (:invoice_id, 'pipeline_completed', 'SYSTEM', 'invoice',
                     :entity_id, :after_state, :details)
            """),
            {
                "invoice_id": invoice_id,
                "entity_id": str(invoice_id),
                "after_state": json.dumps({
                    "total_items": state.get("total_items", 0),
                    "auto_approved": len(state.get("auto_approved_ids", [])),
                    "human_reviewed": len(state.get("paused_item_ids", [])),
                }),
                "details": "Full pipeline completed successfully",
            },
        )
    except Exception as e:
        logger.warning("Audit log insert failed: %s", e)

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
) -> None:
    """Store a classification result as a TransactionState row.

    Uses actual TransactionState column names: final_hs_code, final_confidence,
    review_reason, status, current_node.
    """
    needs_review = confidence < 0.85

    await db.execute(
        text("""
            INSERT INTO transaction_states
                (invoice_id, line_item_id, status, current_node,
                 final_hs_code, final_confidence, needs_review, review_reason)
            VALUES
                (:invoice_id, :line_item_id, :status, 'classify',
                 :code, :confidence, :needs_review, :reasoning)
        """),
        {
            "invoice_id": invoice_id,
            "line_item_id": line_item_id,
            "status": "pending_review" if needs_review else "classified",
            "code": code,
            "confidence": confidence,
            "needs_review": needs_review,
            "reasoning": reasoning,
        },
    )
