"""Invoice and LineItem ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.audit import AuditLog
    from app.models.classification import ExtractionResult, TransactionState


class Invoice(Base):
    """Uploaded commercial invoice document."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/pdf")
    file_size_bytes: Mapped[Optional[int]] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="uploaded", index=True,
    )
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────
    line_items: Mapped[List["LineItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", lazy="selectin",
    )
    extraction_results: Mapped[List["ExtractionResult"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", lazy="selectin",
    )
    transaction_states: Mapped[List["TransactionState"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", lazy="selectin",
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", lazy="selectin",
    )

    __table_args__ = (
        Index("ix_invoices_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id!s} filename={self.filename!r}>"


class LineItem(Base):
    """Individual line item extracted from an invoice."""

    __tablename__ = "line_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_number: Mapped[int] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Optional[float]] = mapped_column(Numeric(14, 4), nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    unit_price: Mapped[Optional[float]] = mapped_column(Numeric(14, 4), nullable=True)
    total_price: Mapped[Optional[float]] = mapped_column(Numeric(14, 4), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True, default="USD")
    country_of_origin: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    hs_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────
    invoice: Mapped["Invoice"] = relationship(back_populates="line_items")

    __table_args__ = (
        Index("ix_line_items_invoice_line", "invoice_id", "line_number", unique=True),
    )

    def __repr__(self) -> str:
        return f"<LineItem id={self.id!s} line={self.line_number}>"
