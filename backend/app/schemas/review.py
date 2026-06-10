"""Human-review queue Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.classification import CandidatePath


class ReviewQueueItem(BaseModel):
    """A single item in the human-review queue."""

    model_config = ConfigDict(from_attributes=True)

    transaction_id: uuid.UUID
    invoice_id: Optional[uuid.UUID] = None
    description: str
    recommended_hs_code: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    review_reason: Optional[str] = None
    candidates: list[CandidatePath] = Field(default_factory=list)
    status: str = "pending_review"
    created_at: datetime
    client_name: Optional[str] = None


class ApproveRequest(BaseModel):
    """Approve a classification as-is."""

    reviewer: str = Field(..., min_length=1, max_length=256, description="Reviewer identifier")
    notes: Optional[str] = Field(None, max_length=2000, description="Optional reviewer notes")


class ModifyRequest(BaseModel):
    """Approve with a corrected HS code."""

    reviewer: str = Field(..., min_length=1, max_length=256)
    corrected_hs_code: str = Field(
        ...,
        min_length=4,
        max_length=16,
        description="Corrected HS tariff code",
    )
    reason: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Justification for the correction",
    )
    notes: Optional[str] = Field(None, max_length=2000)


class RejectRequest(BaseModel):
    """Reject a classification entirely and flag for re-processing."""

    reviewer: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Reason for rejection",
    )
    request_reclassify: bool = Field(
        True,
        description="Whether to automatically re-queue for classification",
    )
