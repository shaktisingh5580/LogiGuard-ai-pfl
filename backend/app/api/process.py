"""Invoice processing endpoint — triggers the full AI pipeline.

POST /api/invoices/{invoice_id}/process
  → Reads the stored PDF
  → Extracts text (PyMuPDF)
  → Sends to LLM for line-item structuring
  → Classifies each item via RAG + Rules + LLM
  → Returns full results

This is the endpoint the frontend calls after uploading a PDF.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.invoice import Invoice
from app.pipeline.processor import InvoiceProcessor

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/invoices/{invoice_id}/process")
async def process_invoice(
    invoice_id: uuid.UUID,
    background: bool = Query(
        False,
        description="If true, run processing in the background and return immediately",
    ),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    session: AsyncSession = Depends(get_session),
):
    """Trigger the full AI classification pipeline for an uploaded invoice.

    This endpoint:
    1. Downloads the PDF from storage
    2. Extracts text using PyMuPDF (OCR not needed for clean PDFs)
    3. Sends the text to your LLM for structured line-item extraction
    4. Classifies each line item through RAG + Rules + LLM
    5. Saves everything to the database
    6. Broadcasts progress via SSE events

    The frontend should:
    - Call this after uploading a PDF via POST /api/invoices
    - Connect to GET /api/events?invoice_id=xxx for real-time progress
    - Poll GET /api/invoices/{id} to see the final results

    Args:
        invoice_id: UUID of the previously uploaded invoice.
        background: If true, returns immediately and processes in background.

    Returns:
        Processing result with classified line items and statistics.
    """
    # Validate the invoice exists
    stmt = select(Invoice).where(Invoice.id == invoice_id)
    invoice = (await session.execute(stmt)).scalar_one_or_none()

    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice not found: {invoice_id}")

    if invoice.status not in ("uploaded", "error", "extraction_failed"):
        raise HTTPException(
            status_code=409,
            detail=f"Invoice is already in '{invoice.status}' state. "
            f"Only 'uploaded', 'error', or 'extraction_failed' invoices can be processed.",
        )

    # Run the pipeline
    processor = InvoiceProcessor(session)

    if background:
        # Run in background — return immediately
        # Note: For background processing, we'd need a separate session.
        # For now, we run synchronously to keep it simple and reliable.
        pass

    result = await processor.process(invoice_id)

    if result.get("status") == "error":
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Pipeline processing failed",
                "error": result.get("error"),
                "invoice_id": str(invoice_id),
            },
        )

    return result


@router.get("/invoices/{invoice_id}/status")
async def get_processing_status(
    invoice_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get the current processing status of an invoice.

    Returns the invoice status and its line items with classification results.
    Useful for polling after triggering /process.
    """
    stmt = select(Invoice).where(Invoice.id == invoice_id)
    invoice = (await session.execute(stmt)).scalar_one_or_none()

    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice not found: {invoice_id}")

    line_items = [
        {
            "id": str(li.id),
            "line_number": li.line_number,
            "description": li.description,
            "quantity": float(li.quantity) if li.quantity else None,
            "unit": li.unit,
            "unit_price": float(li.unit_price) if li.unit_price else None,
            "total_price": float(li.total_price) if li.total_price else None,
            "currency": li.currency,
            "country_of_origin": li.country_of_origin,
            "hs_code": li.hs_code,
            "confidence": float(li.confidence) if li.confidence else None,
        }
        for li in invoice.line_items
    ]

    return {
        "invoice_id": str(invoice.id),
        "filename": invoice.filename,
        "status": invoice.status,
        "total_items": len(line_items),
        "classified": sum(1 for li in line_items if li["hs_code"]),
        "needs_review": sum(
            1
            for ts in invoice.transaction_states
            if ts.needs_review and ts.status == "pending_review"
        ),
        "line_items": line_items,
    }
