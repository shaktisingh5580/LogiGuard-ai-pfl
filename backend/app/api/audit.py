"""Audit log retrieval endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.audit import AuditLog

router = APIRouter()


class AuditLogResponse(BaseModel):
    """Public representation of an audit log entry."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: Optional[uuid.UUID] = None
    client_id: Optional[uuid.UUID] = None
    action: str
    actor: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    before_state: Optional[dict[str, Any]] = None
    after_state: Optional[dict[str, Any]] = None
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime


@router.get("/audit/{invoice_id}", response_model=list[AuditLogResponse])
async def get_audit_trail(
    invoice_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[AuditLogResponse]:
    """Return the full audit trail for a given invoice, newest first.

    Each entry captures a before/after state snapshot so reviewers and
    compliance officers can reconstruct the complete decision history.
    """
    stmt = (
        select(AuditLog)
        .where(AuditLog.invoice_id == invoice_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    logs = result.scalars().all()

    if not logs:
        # Verify the invoice ID exists before returning empty
        # (a truly non-existent invoice should 404)
        from app.models.invoice import Invoice

        inv_stmt = select(Invoice.id).where(Invoice.id == invoice_id)
        inv = (await session.execute(inv_stmt)).scalar_one_or_none()
        if inv is None:
            raise HTTPException(status_code=404, detail="Invoice not found")

    return [AuditLogResponse.model_validate(log) for log in logs]
