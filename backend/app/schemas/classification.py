"""Classification request / response Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class CandidatePath(BaseModel):
    """A single candidate HS classification path returned by the ensemble."""

    hs_code: str = Field(..., description="Full HS tariff code (up to 10 digits)")
    description: str = Field(..., description="Tariff schedule description for this code")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Ensemble confidence score")
    strategy: str = Field(..., description="Strategy that produced this candidate")
    reasoning: Optional[str] = Field(None, description="LLM reasoning chain")
    duty_rate_percent: Optional[float] = Field(None, description="Applicable duty rate")
    supporting_rules: list[str] = Field(
        default_factory=list,
        description="References to tariff rules / explanatory notes used",
    )


class ExcludedCandidate(BaseModel):
    """A candidate path that was considered but excluded with justification."""

    hs_code: str
    description: str
    exclusion_reason: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class ClassifyRequest(BaseModel):
    """Direct classification request (without an invoice upload)."""

    description: str = Field(
        ...,
        min_length=3,
        max_length=4000,
        description="Product description to classify",
    )
    country_of_origin: Optional[str] = Field(
        None,
        min_length=2,
        max_length=3,
        description="ISO 3166-1 alpha-2/3 country code",
    )
    additional_context: Optional[str] = Field(
        None,
        max_length=2000,
        description="Extra context such as material composition, intended use, etc.",
    )
    client_id: Optional[uuid.UUID] = None
    force_reclassify: bool = Field(
        False,
        description="Bypass the classification cache and run a full pipeline",
    )


class ClassifyResponse(BaseModel):
    """Result of a classification run."""

    model_config = ConfigDict(from_attributes=True)

    transaction_id: uuid.UUID = Field(..., description="ID of the TransactionState record")
    invoice_id: Optional[uuid.UUID] = None
    description: str
    recommended_hs_code: str = Field(..., description="Top-ranked HS code")
    confidence: float = Field(..., ge=0.0, le=1.0)
    needs_review: bool = Field(False, description="True when confidence < threshold")
    review_reason: Optional[str] = None
    candidates: list[CandidatePath] = Field(default_factory=list)
    excluded: list[ExcludedCandidate] = Field(default_factory=list)
    duty_rate_percent: Optional[float] = None
    processing_time_ms: Optional[int] = None
    cache_hit: bool = False
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
