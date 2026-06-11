"""Full invoice processing pipeline — Extract → Structure → Classify.

Orchestrates the complete end-to-end flow:
  1. Read the PDF from storage
  2. Extract raw text (PyMuPDF for clean PDFs, Tesseract OCR fallback)
  3. Send raw text to LLM for structured line-item extraction
  4. Classify each line item through the RAG + Rules + LLM pipeline
  5. Persist everything to the database
  6. Broadcast real-time progress via SSE events

Usage::

    processor = InvoiceProcessor(session)
    result = await processor.process(invoice_id)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.events import EventType, PipelineEvent, get_event_publisher
from app.core.llm import get_llm_provider
from app.core.storage import get_storage_backend
from app.models.classification import ExtractionResult, TransactionState
from app.models.invoice import Invoice, LineItem
from app.prompts.templates import EXTRACTION_PROMPT, COMPARATIVE_CLASSIFICATION_PROMPT
from app.rag.retriever import MultiPathRetriever
from app.rules.engine import TariffRuleEngine

logger = logging.getLogger(__name__)


# ── PDF Text Extraction ─────────────────────────────────────────────────────

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using PyMuPDF (fast, no external deps).

    Falls back to a simple bytes-decode if PyMuPDF is not installed.
    """
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages: list[str] = []
        for page in doc:
            pages.append(page.get_text("text"))
        doc.close()
        full_text = "\n\n".join(pages)
        logger.info("PyMuPDF extracted %d characters from %d pages", len(full_text), len(pages))
        return full_text
    except ImportError:
        logger.warning("PyMuPDF not installed — attempting raw text decode")
        # Last resort: try to decode as text (won't work for real PDFs)
        return pdf_bytes.decode("utf-8", errors="replace")


# ── LLM Structuring ─────────────────────────────────────────────────────────

STRUCTURING_PROMPT = """\
You are an expert document parser specializing in commercial invoices.
You will receive the raw OCR/extracted text of a commercial invoice.
Your task is to extract ALL line items into a structured JSON format.

For EACH line item, extract:
1. description: The commodity/product description (exact text from invoice)
2. quantity: Numeric quantity (float)
3. unit: Unit of measurement (PCS, KGS, MTR, SET, etc.)
4. unit_price: Price per unit (float, no currency symbol)
5. total: Line total (float, no currency symbol)
6. country_of_origin: If mentioned anywhere (ISO 2-letter code), else null

Also extract invoice-level metadata:
- invoice_number
- invoice_date (YYYY-MM-DD)
- seller name
- buyer name
- currency (3-letter ISO code)

OUTPUT FORMAT (strict JSON, no markdown fences):
{
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "seller": "string or null",
  "buyer": "string or null",
  "currency": "USD",
  "country_of_origin": "XX or null",
  "line_items": [
    {
      "line_number": 1,
      "description": "string",
      "quantity": 0.0,
      "unit": "PCS",
      "unit_price": 0.0,
      "total": 0.0,
      "country_of_origin": "XX or null"
    }
  ]
}

RULES:
- Extract EVERY line item, even if partially visible
- Preserve exact commodity descriptions — do NOT paraphrase or summarize
- If a field is missing or unreadable, use null
- Quantities must be numeric (not text like "five hundred")
- Return ONLY valid JSON — no commentary, no markdown code fences"""


async def structure_invoice_text(raw_text: str) -> dict[str, Any]:
    """Send raw invoice text to LLM for structured extraction.

    Returns a dict with 'line_items' list and invoice-level metadata.
    """
    llm = get_llm_provider()

    response = await llm.complete(
        messages=[
            {"role": "system", "content": STRUCTURING_PROMPT},
            {
                "role": "user",
                "content": (
                    "Extract all line items from this invoice text:\n\n"
                    f"---BEGIN INVOICE TEXT---\n{raw_text}\n---END INVOICE TEXT---"
                ),
            },
        ],
        temperature=0.0,
        max_tokens=4096,
    )

    raw_content = response.content.strip()
    # Strip markdown code fences if the LLM wraps its output
    if raw_content.startswith("```"):
        raw_content = raw_content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        return json.loads(raw_content)
    except json.JSONDecodeError as e:
        logger.error("LLM structuring returned invalid JSON: %s", e)
        logger.debug("Raw LLM response: %s", raw_content[:500])
        return {"line_items": [], "parse_error": str(e), "raw_response": raw_content[:1000]}


# ── Per-Item Classification ─────────────────────────────────────────────────

async def classify_single_item(
    session: AsyncSession,
    description: str,
    country_of_origin: str | None = None,
) -> dict[str, Any]:
    """Run the full RAG + Rules + LLM classification for a single description.

    Returns a dict with: code, confidence, reasoning, needs_review, review_reason
    """
    llm = get_llm_provider()
    retriever = MultiPathRetriever(session, llm)
    rule_engine = TariffRuleEngine(session)

    # Step 1: RAG retrieval
    candidates_raw = await retriever.retrieve(
        description=description,
        invoice_date=date.today(),
        jurisdiction=country_of_origin or "IN",
    )

    if not candidates_raw:
        return {
            "code": "REVIEW_REQUIRED",
            "confidence": 0.0,
            "reasoning": "No matching tariff headings found in database",
            "needs_review": True,
            "review_reason": "No classification candidates found in tariff database",
        }

    # Step 2: Rule Engine filter
    filtered = await rule_engine.filter_candidates(candidates_raw, description)

    if not filtered.valid_candidates:
        return {
            "code": "REVIEW_REQUIRED",
            "confidence": 0.0,
            "reasoning": "All candidates excluded by rule engine",
            "needs_review": True,
            "review_reason": "All classification candidates were excluded by tariff rules",
        }

    # Step 3: LLM Comparative Reasoning
    candidates_context = "\n\n".join(
        f"--- Option {i + 1} ---\n"
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
                        f"DESCRIPTION: {description}\n"
                        f"COUNTRY OF ORIGIN: {country_of_origin or 'Not specified'}\n\n"
                        f"CANDIDATE CLASSIFICATIONS:\n{candidates_context}\n\n"
                        f'Return JSON: {{"code": "XXXX.XX.XX", "confidence": 0.XX, "reasoning": "..."}}'
                    ),
                },
            ],
            temperature=0.0,
        )

        raw = llm_response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        parsed = json.loads(raw)

        code = parsed.get("code", "0000.00.0000")
        confidence = float(parsed.get("confidence", 0.5))
        reasoning = parsed.get("reasoning", raw)
        needs_review = confidence < 0.85

        return {
            "code": code,
            "confidence": confidence,
            "reasoning": reasoning,
            "needs_review": needs_review,
            "review_reason": (
                f"Confidence {confidence:.0%} below auto-approve threshold (85%)"
                if needs_review
                else None
            ),
        }

    except Exception as e:
        logger.error("LLM classification failed for '%s': %s", description[:80], e)
        return {
            "code": "REVIEW_REQUIRED",
            "confidence": 0.0,
            "reasoning": f"LLM classification error: {e}",
            "needs_review": True,
            "review_reason": f"LLM classification error: {e}",
        }


# ── Main Pipeline Orchestrator ───────────────────────────────────────────────

class InvoiceProcessor:
    """Orchestrates the full pipeline for a single invoice.

    Usage::

        async with get_session() as session:
            processor = InvoiceProcessor(session)
            result = await processor.process(invoice_id)
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._publisher = get_event_publisher()

    async def _emit(self, event_type: EventType, invoice_id: uuid.UUID, data: dict | None = None):
        """Publish an SSE event."""
        event = PipelineEvent(event_type=event_type, invoice_id=invoice_id, data=data)
        await self._publisher.publish(event)

    async def process(self, invoice_id: uuid.UUID) -> dict[str, Any]:
        """Run the full Extract → Structure → Classify pipeline.

        Args:
            invoice_id: UUID of the uploaded invoice.

        Returns:
            Summary dict with processing results and statistics.
        """
        start_time = time.monotonic()

        # ── 1. Load the invoice record ───────────────────────────
        stmt = select(Invoice).where(Invoice.id == invoice_id)
        invoice = (await self._session.execute(stmt)).scalar_one_or_none()
        if not invoice:
            raise ValueError(f"Invoice not found: {invoice_id}")

        # Update status to processing
        invoice.status = "processing"
        await self._session.flush()

        await self._emit(EventType.EXTRACTION_STARTED, invoice_id, {
            "message": "Starting text extraction from PDF...",
            "filename": invoice.filename,
        })

        try:
            # ── 2. Download PDF from storage ─────────────────────
            storage = get_storage_backend()
            pdf_bytes = await storage.download(invoice.storage_key)
            logger.info("Downloaded %d bytes for invoice %s", len(pdf_bytes), invoice_id)

            # ── 3. Extract raw text ──────────────────────────────
            raw_text = extract_text_from_pdf_bytes(pdf_bytes)
            if not raw_text.strip():
                raise ValueError("PDF text extraction returned empty result — the PDF may be image-only")

            # Save the raw text to the invoice record
            invoice.raw_text = raw_text
            await self._session.flush()

            await self._emit(EventType.EXTRACTION_COMPLETED, invoice_id, {
                "message": "Text extraction complete",
                "characters_extracted": len(raw_text),
            })

            # ── 4. LLM Structuring (extract line items) ──────────
            await self._emit(EventType.CLASSIFICATION_STARTED, invoice_id, {
                "message": "Sending extracted text to AI for structuring...",
                "step": "structuring",
            })

            structured = await structure_invoice_text(raw_text)

            if structured.get("parse_error"):
                logger.error("Structuring failed: %s", structured["parse_error"])
                raise ValueError(f"LLM structuring failed: {structured['parse_error']}")

            line_items_data = structured.get("line_items", [])
            if not line_items_data:
                logger.warning("No line items found in invoice %s", invoice_id)
                # Still save the extraction result
                extraction = ExtractionResult(
                    invoice_id=invoice_id,
                    extractor_name="llm_structuring",
                    extracted_fields=structured,
                    confidence=0.0,
                )
                self._session.add(extraction)
                invoice.status = "extraction_failed"
                await self._session.flush()

                await self._emit(EventType.PIPELINE_ERROR, invoice_id, {
                    "message": "No line items could be extracted from this invoice",
                    "error": "empty_extraction",
                })

                return {
                    "invoice_id": str(invoice_id),
                    "status": "extraction_failed",
                    "line_items_found": 0,
                    "error": "No line items extracted",
                }

            # Save the extraction result
            extraction = ExtractionResult(
                invoice_id=invoice_id,
                extractor_name="llm_structuring",
                extracted_fields=structured,
                confidence=0.95,
            )
            self._session.add(extraction)

            # ── 5. Persist line items ────────────────────────────
            # Determine the country_of_origin (invoice-level or per-item)
            invoice_origin = structured.get("country_of_origin")

            db_line_items: list[LineItem] = []
            for idx, item in enumerate(line_items_data):
                li = LineItem(
                    invoice_id=invoice_id,
                    line_number=item.get("line_number", idx + 1),
                    description=item.get("description", "Unknown"),
                    quantity=item.get("quantity"),
                    unit=item.get("unit"),
                    unit_price=item.get("unit_price"),
                    total_price=item.get("total"),
                    currency=structured.get("currency", "USD"),
                    country_of_origin=item.get("country_of_origin") or invoice_origin,
                    raw_data=item,
                )
                self._session.add(li)
                db_line_items.append(li)

            await self._session.flush()

            logger.info(
                "Extracted and saved %d line items for invoice %s",
                len(db_line_items),
                invoice_id,
            )

            await self._emit(EventType.EXTRACTION_COMPLETED, invoice_id, {
                "message": f"Extracted {len(db_line_items)} line items",
                "line_items_count": len(db_line_items),
                "step": "structuring_complete",
            })

            # ── 6. Classify each line item ───────────────────────
            total_items = len(db_line_items)
            classified_count = 0
            review_count = 0
            results: list[dict[str, Any]] = []

            for idx, li in enumerate(db_line_items):
                await self._emit(EventType.CLASSIFICATION_STARTED, invoice_id, {
                    "message": f"Classifying item {idx + 1}/{total_items}: {li.description[:60]}...",
                    "current_item": idx + 1,
                    "total_items": total_items,
                    "description": li.description[:100],
                    "line_item_id": str(li.id),
                })

                # Run the classification
                result = await classify_single_item(
                    session=self._session,
                    description=li.description,
                    country_of_origin=li.country_of_origin,
                )

                # Update line item with classification result
                li.hs_code = result["code"]
                li.confidence = result["confidence"]

                # Create a TransactionState for auditing
                txn = TransactionState(
                    invoice_id=invoice_id,
                    line_item_id=li.id,
                    status="completed" if not result["needs_review"] else "pending_review",
                    current_node="classification_complete",
                    final_hs_code=result["code"],
                    final_confidence=result["confidence"],
                    needs_review=result["needs_review"],
                    review_reason=result.get("review_reason"),
                    graph_state={
                        "reasoning": result["reasoning"],
                        "description": li.description,
                    },
                    completed_at=datetime.now(timezone.utc),
                )
                self._session.add(txn)

                classified_count += 1
                if result["needs_review"]:
                    review_count += 1

                results.append({
                    "line_number": li.line_number,
                    "description": li.description[:100],
                    "hs_code": result["code"],
                    "confidence": result["confidence"],
                    "needs_review": result["needs_review"],
                })

                await self._emit(EventType.CLASSIFICATION_COMPLETED, invoice_id, {
                    "message": f"Classified {idx + 1}/{total_items}",
                    "current_item": idx + 1,
                    "total_items": total_items,
                    "hs_code": result["code"],
                    "confidence": result["confidence"],
                    "needs_review": result["needs_review"],
                    "line_item_id": str(li.id),
                })

            # ── 7. Finalize ──────────────────────────────────────
            invoice.status = "classified"
            await self._session.flush()

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            await self._emit(EventType.COMPLETION_DONE, invoice_id, {
                "message": "Pipeline complete",
                "total_items": total_items,
                "classified": classified_count,
                "needs_review": review_count,
                "auto_approved": classified_count - review_count,
                "processing_time_ms": elapsed_ms,
            })

            logger.info(
                "Pipeline complete for invoice %s: %d items, %d need review (%.1fs)",
                invoice_id,
                total_items,
                review_count,
                elapsed_ms / 1000,
            )

            return {
                "invoice_id": str(invoice_id),
                "status": "classified",
                "total_items": total_items,
                "classified": classified_count,
                "needs_review": review_count,
                "auto_approved": classified_count - review_count,
                "processing_time_ms": elapsed_ms,
                "results": results,
            }

        except Exception as e:
            logger.exception("Pipeline failed for invoice %s", invoice_id)
            invoice.status = "error"
            await self._session.flush()

            await self._emit(EventType.PIPELINE_ERROR, invoice_id, {
                "message": f"Pipeline error: {e}",
                "error": str(e),
            })

            return {
                "invoice_id": str(invoice_id),
                "status": "error",
                "error": str(e),
            }
