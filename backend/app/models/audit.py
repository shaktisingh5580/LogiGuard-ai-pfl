"""Audit and Client ORM models.

Provides an immutable audit trail of every significant action in the system,
along with a lightweight client/tenant table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice


class Client(Base):
    """A client / tenant that submits invoices for classification."""

    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    settings: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
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
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        back_populates="client", cascade="all, delete-orphan", lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Client code={self.code!r} name={self.name!r}>"


class AuditLog(Base):
    """Immutable audit trail entry.

    Every significant action (upload, classify, approve, reject, modify) is
    captured with before/after snapshots so the full history of a
    classification decision can be reconstructed.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    before_state: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────
    invoice: Mapped[Optional["Invoice"]] = relationship(back_populates="audit_logs")
    client: Mapped[Optional["Client"]] = relationship(back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_invoice_action", "invoice_id", "action"),
        Index("ix_audit_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog action={self.action!r} entity={self.entity_type}:{self.entity_id}>"
