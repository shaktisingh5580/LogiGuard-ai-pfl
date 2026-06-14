"""Tariff reference-data ORM models.

These tables store the Harmonized System tariff tree, epoch-versioned rule
snapshots, duty rates, and gateway field mappings used by the classification
pipeline.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TariffEpoch(Base):
    """A versioned snapshot of the tariff schedule (e.g. HS 2022, HS 2027).

    All rules and duty rates reference an epoch so historical lookups stay
    consistent when schedules are updated.
    """

    __tablename__ = "tariff_epochs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiration_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────
    rules: Mapped[List["TariffRule"]] = relationship(
        back_populates="epoch", cascade="all, delete-orphan", lazy="selectin",
    )
    duty_rates: Mapped[List["DutyRate"]] = relationship(
        back_populates="epoch", cascade="all, delete-orphan", lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<TariffEpoch {self.name!r} v{self.version}>"


class TariffRule(Base):
    """A single rule / explanatory note from the tariff schedule.

    The `embedding` column stores a pgvector embedding for semantic search
    during RAG-based classification.
    """

    __tablename__ = "tariff_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    epoch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tariff_epochs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hs_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    heading: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    section: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    chapter: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    explanatory_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    general_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(768), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Rule engine specific fields
    rule_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    applies_to_section: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    applies_to_chapter: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    applies_to_heading: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    condition_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    condition_parameters: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    statutory_reference: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    jurisdiction: Mapped[Optional[str]] = mapped_column(String(3), nullable=True, default="IN")
    effective_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────
    epoch: Mapped["TariffEpoch"] = relationship(back_populates="rules")

    __table_args__ = (
        Index("ix_tariff_rules_epoch_hs", "epoch_id", "hs_code"),
        Index(
            "ix_tariff_rules_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<TariffRule hs={self.hs_code!r} chunk={self.chunk_index}>"


class DutyRate(Base):
    """Duty rate schedule associated with a specific HS code and epoch."""

    __tablename__ = "duty_rates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    epoch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tariff_epochs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hs_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    duty_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ad_valorem",
    )
    rate_percent: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    specific_rate: Mapped[Optional[float]] = mapped_column(Numeric(14, 4), nullable=True)
    specific_unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    preferential_programs: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────
    epoch: Mapped["TariffEpoch"] = relationship(back_populates="duty_rates")

    __table_args__ = (
        Index("ix_duty_epoch_hs_country", "epoch_id", "hs_code", "country_code"),
    )

    def __repr__(self) -> str:
        return f"<DutyRate hs={self.hs_code!r} rate={self.rate_percent}%>"


class HSTariffTree(Base):
    """Hierarchical HS tariff tree — sections → chapters → headings → subheadings.

    Stored as an adjacency-list with a materialized ``path`` column for fast
    ancestor / descendant queries.
    """

    __tablename__ = "hs_tariff_tree"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hs_tariff_tree.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    level: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )  # 1=section, 2=chapter, 3=heading, 4=subheading, 5=tariff line
    description: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(
        String(256), nullable=False, index=True,
    )  # e.g. "01.01.01.0101.10"
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    # ── Self-referential relationship ─────────────────────────
    children: Mapped[List["HSTariffTree"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan",
    )
    parent: Mapped[Optional["HSTariffTree"]] = relationship(
        back_populates="children", remote_side=[id],
    )

    __table_args__ = (
        UniqueConstraint("code", "level", name="uq_hs_tree_code_level"),
    )

    def __repr__(self) -> str:
        return f"<HSTariffTree code={self.code!r} level={self.level}>"


class GatewayFieldMapping(Base):
    """Maps fields between external customs gateway formats and the internal schema.

    Each gateway (e.g. ACE, CHIEF, ASYCUDA) has its own field naming; this
    table provides the translation layer.
    """

    __tablename__ = "gateway_field_mappings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    gateway_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_field: Mapped[str] = mapped_column(String(256), nullable=False)
    internal_field: Mapped[str] = mapped_column(String(256), nullable=False)
    transform_rule: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_value: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "gateway_name", "external_field",
            name="uq_gateway_field",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GatewayFieldMapping {self.gateway_name}:"
            f"{self.external_field} → {self.internal_field}>"
        )
