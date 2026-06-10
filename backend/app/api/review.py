"""Human-review queue endpoints.

Provides CRUD-like operations for the review queue: list pending items,
view details, and approve / modify / reject classifications.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.audit import AuditLog
from app.models.classification import TransactionState
from app.schemas.classification import CandidatePath
from app.schemas.review import (
    ApproveRequest,
    ModifyRequest,
    RejectRequest,
    ReviewQueueItem,
)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────

def _txn_to_queue_item(txn: TransactionState) -> ReviewQueueItem:
    """Map a TransactionState ORM instance to a ReviewQueueItem schema."""
    candidates: list[CandidatePath] = []
    for vote in txn.ensemble_votes:
        candidates.append(
            CandidatePath(
                hs_code=vote.hs_code,
                description=vote.reasoning or "",
                confidence=float(vote.confidence),
                strategy=vote.strategy_name,
                reasoning=vote.reasoning,
            )
        )

    return ReviewQueueItem(
        transaction_id=txn.id,
        invoice_id=txn.invoice_id,
        description=(
            txn.graph_state.get("description", "") if txn.graph_state else ""
        ),
        recommended_hs_code=txn.final_hs_code or "0000.00.0000",
        confidence=float(txn.final_confidence) if txn.final_confidence else 0.0,
        review_reason=txn.review_reason,
        candidates=candidates,
        status=txn.status,
        created_at=txn.started_at,
    )


async def _get_txn_or_404(
    txn_id: uuid.UUID,
    session: AsyncSession,
) -> TransactionState:
    """Fetch a TransactionState by ID or raise 404."""
    stmt = select(TransactionState).where(TransactionState.id == txn_id)
    txn = (await session.execute(stmt)).scalar_one_or_none()
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return txn


async def _record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor: str,
    txn: TransactionState,
    before: dict[str, Any],
    after: dict[str, Any],
    details: str | None = None,
) -> None:
    """Persist an immutable audit log entry."""
    session.add(
        AuditLog(
            invoice_id=txn.invoice_id,
            action=action,
            actor=actor,
            entity_type="transaction_state",
            entity_id=str(txn.id),
            before_state=before,
            after_state=after,
            details=details,
        )
    )


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/queue", response_model=list[ReviewQueueItem])
async def list_review_queue(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> list[ReviewQueueItem]:
    """Return items waiting for human review, newest first."""
    stmt = (
        select(TransactionState)
        .where(TransactionState.needs_review.is_(True))
        .where(TransactionState.status.in_(["pending", "pending_review"]))
        .order_by(TransactionState.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    transactions = result.scalars().all()
    return [_txn_to_queue_item(t) for t in transactions]


@router.get("/{transaction_id}", response_model=ReviewQueueItem)
async def get_review_item(
    transaction_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ReviewQueueItem:
    """Fetch a single review item by transaction ID."""
    txn = await _get_txn_or_404(transaction_id, session)
    return _txn_to_queue_item(txn)


@router.post("/{transaction_id}/approve", response_model=ReviewQueueItem)
async def approve_classification(
    transaction_id: uuid.UUID,
    body: ApproveRequest,
    session: AsyncSession = Depends(get_session),
) -> ReviewQueueItem:
    """Approve the classification as-is."""
    txn = await _get_txn_or_404(transaction_id, session)

    if txn.status not in ("pending", "pending_review"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve transaction in '{txn.status}' status",
        )

    before = {"status": txn.status, "needs_review": txn.needs_review}

    txn.status = "approved"
    txn.needs_review = False
    txn.reviewed_by = body.reviewer
    txn.reviewed_at = datetime.now(timezone.utc)
    txn.completed_at = datetime.now(timezone.utc)

    after = {"status": txn.status, "needs_review": txn.needs_review}

    await _record_audit(
        session,
        action="approve",
        actor=body.reviewer,
        txn=txn,
        before=before,
        after=after,
        details=body.notes,
    )

    await session.flush()
    await session.refresh(txn)
    return _txn_to_queue_item(txn)


@router.post("/{transaction_id}/modify", response_model=ReviewQueueItem)
async def modify_classification(
    transaction_id: uuid.UUID,
    body: ModifyRequest,
    session: AsyncSession = Depends(get_session),
) -> ReviewQueueItem:
    """Approve with a corrected HS code."""
    txn = await _get_txn_or_404(transaction_id, session)

    if txn.status not in ("pending", "pending_review"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot modify transaction in '{txn.status}' status",
        )

    before = {
        "status": txn.status,
        "final_hs_code": txn.final_hs_code,
        "needs_review": txn.needs_review,
    }

    txn.status = "modified"
    txn.final_hs_code = body.corrected_hs_code
    txn.needs_review = False
    txn.reviewed_by = body.reviewer
    txn.reviewed_at = datetime.now(timezone.utc)
    txn.completed_at = datetime.now(timezone.utc)
    txn.review_reason = body.reason

    after = {
        "status": txn.status,
        "final_hs_code": txn.final_hs_code,
        "needs_review": txn.needs_review,
    }

    await _record_audit(
        session,
        action="modify",
        actor=body.reviewer,
        txn=txn,
        before=before,
        after=after,
        details=body.notes,
    )

    await session.flush()
    await session.refresh(txn)
    return _txn_to_queue_item(txn)


@router.post("/{transaction_id}/reject", response_model=ReviewQueueItem)
async def reject_classification(
    transaction_id: uuid.UUID,
    body: RejectRequest,
    session: AsyncSession = Depends(get_session),
) -> ReviewQueueItem:
    """Reject a classification and optionally re-queue for processing."""
    txn = await _get_txn_or_404(transaction_id, session)

    if txn.status not in ("pending", "pending_review"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reject transaction in '{txn.status}' status",
        )

    before = {"status": txn.status, "needs_review": txn.needs_review}

    txn.status = "rejected"
    txn.needs_review = False
    txn.reviewed_by = body.reviewer
    txn.reviewed_at = datetime.now(timezone.utc)
    txn.review_reason = body.reason

    # If re-classification is requested, set status accordingly
    if body.request_reclassify:
        txn.status = "pending_reclassify"

    after = {"status": txn.status, "needs_review": txn.needs_review}

    await _record_audit(
        session,
        action="reject",
        actor=body.reviewer,
        txn=txn,
        before=before,
        after=after,
        details=body.reason,
    )

    await session.flush()
    await session.refresh(txn)
    return _txn_to_queue_item(txn)
