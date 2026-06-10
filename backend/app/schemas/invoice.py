"""Invoice-related Pydantic schemas for request / response validation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class LineItemResponse(BaseModel):
    """Public representation of a single invoice line item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    line_number: int
    description: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    total_price: Optional[float] = None
    currency: Optional[str] = "USD"
    country_of_origin: Optional[str] = None
    hs_code: Optional[str] = None
    confidence: Optional[float] = None
    raw_data: Optional[dict[str, Any]] = None
    created_at: datetime


class InvoiceCreate(BaseModel):
    """Metadata submitted alongside an invoice file upload.

    The actual file is received via ``UploadFile``; this schema captures
    optional structured metadata the caller can provide.
    """

    client_id: Optional[uuid.UUID] = None
    metadata: Optional[dict[str, Any]] = Field(default=None, alias="metadata")


class InvoiceResponse(BaseModel):
    """Full invoice representation returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: Optional[uuid.UUID] = None
    filename: str
    storage_key: str
    content_type: str
    file_size_bytes: Optional[int] = None
    status: str
    raw_text: Optional[str] = None
    metadata: Optional[dict[str, Any]] = Field(default=None, alias="metadata_")
    created_at: datetime
    updated_at: datetime
    line_items: list[LineItemResponse] = Field(default_factory=list)
