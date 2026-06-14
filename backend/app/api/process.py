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

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.invoice import Invoice
from app.pipeline.processor import InvoiceProcessor

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/invoices/{invoice_id}/process", status_code=202)
async def process_invoice(
    invoice_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Trigger the full AI classification pipeline for an uploaded invoice.

    Returns 202 immediately and processes in background so the frontend
    doesn't time out on large invoices. Use SSE events for real-time progress.
    """
    # Validate the invoice exists
    stmt = select(Invoice).where(Invoice.id == invoice_id)
    invoice = (await session.execute(stmt)).scalar_one_or_none()

    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice not found: {invoice_id}")

    if invoice.status in ("processing", "completed", "classified"):
        return {
            "invoice_id": str(invoice_id),
            "status": invoice.status,
            "message": f"Invoice is already in '{invoice.status}' state",
        }

    if invoice.status not in ("uploaded", "error", "extraction_failed", "pending_review"):
        raise HTTPException(
            status_code=409,
            detail=f"Invoice is in '{invoice.status}' state. Only uploaded/error/extraction_failed invoices can be processed.",
        )

    # Mark as processing immediately so the UI updates right away
    invoice.status = "processing"
    await session.flush()  # Write to DB within current transaction
    # The get_session dependency commits on exit — we need the status committed now
    # so the background task can see it; commit explicitly here.
    await session.commit()

    # Run the full pipeline in the background
    async def _run_pipeline():
        from app.database import async_session_factory
        async with async_session_factory() as bg_session:
            processor = InvoiceProcessor(bg_session)
            await processor.process(invoice_id)

    background_tasks.add_task(_run_pipeline)

    return {
        "invoice_id": str(invoice_id),
        "status": "processing",
        "message": "Pipeline started in background. Listen to SSE events for progress.",
    }


@router.get("/invoices/{invoice_id}/status")
async def get_processing_status(
    invoice_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get the current processing status of an invoice.

    Returns the invoice status and its line items with classification results.
    Useful for polling after triggering /process.
    """
    from sqlalchemy.orm import selectinload
    stmt = (
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(
            selectinload(Invoice.line_items),
            selectinload(Invoice.transaction_states)
        )
    )
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
