"""Invoice upload endpoint.

Accepts a multipart file upload, persists the document to object storage,
creates the Invoice record, and returns the invoice ID for downstream
processing.
"""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.core.storage import get_storage_backend
from app.models.invoice import Invoice
from app.schemas.invoice import InvoiceResponse

router = APIRouter()

_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/invoices", response_model=InvoiceResponse, status_code=201)
async def upload_invoice(
    file: UploadFile = File(..., description="Invoice document (PDF or image)"),
    client_id: str | None = Form(None, description="Optional client UUID"),
    session: AsyncSession = Depends(get_session),
) -> InvoiceResponse:
    """Upload an invoice document for processing.

    The file is stored in object storage and an ``Invoice`` record is created
    in the database.  The caller receives the invoice ID which can then be
    passed to the classification pipeline.
    """
    # ── Validate content type ─────────────────────────────────
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported file type '{file.content_type}'. "
                f"Accepted: {', '.join(sorted(_ALLOWED_CONTENT_TYPES))}"
            ),
        )

    # ── Read file contents ────────────────────────────────────
    contents = await file.read()
    if len(contents) > _MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {_MAX_FILE_SIZE // (1024 * 1024)} MB.",
        )

    # ── Generate storage key ──────────────────────────────────
    invoice_id = uuid.uuid4()
    storage_key = f"invoices/{invoice_id}/{file.filename}"

    # ── Store file in configured backend (local / S3) ────────
    storage = get_storage_backend()
    await storage.upload(storage_key, contents, content_type=file.content_type or "application/octet-stream")

    # ── Parse optional client_id ──────────────────────────────
    parsed_client_id: uuid.UUID | None = None
    if client_id:
        try:
            parsed_client_id = uuid.UUID(client_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid client_id UUID") from exc

    # ── Persist Invoice record ────────────────────────────────
    invoice = Invoice(
        id=invoice_id,
        client_id=parsed_client_id,
        filename=file.filename or "unknown",
        storage_key=storage_key,
        content_type=file.content_type or "application/octet-stream",
        file_size_bytes=len(contents),
        status="uploaded",
    )
    session.add(invoice)
    await session.flush()
    await session.refresh(invoice)

    return InvoiceResponse.model_validate(invoice)

@router.get("/invoices", response_model=list[InvoiceResponse])
async def list_invoices(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> list[InvoiceResponse]:
    """List uploaded invoices, newest first."""
    stmt = select(Invoice).order_by(Invoice.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    invoices = result.scalars().all()
    return [InvoiceResponse.model_validate(inv) for inv in invoices]

@router.get("/invoices/{invoice_id}/file")
async def get_invoice_file(
    invoice_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download or stream the invoice file."""
    stmt = select(Invoice).where(Invoice.id == invoice_id)
    invoice = (await session.execute(stmt)).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
        
    storage = get_storage_backend()
    try:
        content = await storage.download(invoice.storage_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found in storage")
        
    return Response(content=content, media_type=invoice.content_type)

