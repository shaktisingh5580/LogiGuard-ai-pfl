"""Classification pipeline ORM models.

Covers the full classification lifecycle: transaction state tracking,
ensemble voting, RAG retrieval results, and structured extraction output.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice


class TransactionState(Base):
    """Tracks the state-machine lifecycle of a single classification run.

    Each invoice can have multiple classification runs (e.g. re-classify after
    human review).  The LangGraph checkpoint ID links back to the graph state.
    """

    __tablename__ = "transaction_states"

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
    line_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("line_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True,
    )
    current_node: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    langgraph_checkpoint_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    final_hs_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    final_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    needs_review: Mapped[bool] = mapped_column(default=False, nullable=False)
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    graph_state: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────
    invoice: Mapped["Invoice"] = relationship(back_populates="transaction_states")
    ensemble_votes: Mapped[List["EnsembleVote"]] = relationship(
        back_populates="transaction_state", cascade="all, delete-orphan", lazy="selectin",
    )
    rag_results: Mapped[List["RAGResult"]] = relationship(
        back_populates="transaction_state", cascade="all, delete-orphan", lazy="selectin",
    )

    __table_args__ = (
        Index("ix_txn_invoice_status", "invoice_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<TransactionState id={self.id!s} status={self.status!r}>"


class EnsembleVote(Base):
    """A single vote from one strategy in the ensemble classifier."""

    __tablename__ = "ensemble_votes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    transaction_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transaction_states.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    hs_code: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    token_usage: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────
    transaction_state: Mapped["TransactionState"] = relationship(
        back_populates="ensemble_votes",
    )

    __table_args__ = (
        Index("ix_votes_txn_strategy", "transaction_state_id", "strategy_name"),
    )

    def __repr__(self) -> str:
        return f"<EnsembleVote strategy={self.strategy_name!r} hs={self.hs_code!r}>"


class RAGResult(Base):
    """A single retrieval result surfaced during RAG-based classification."""

    __tablename__ = "rag_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    transaction_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transaction_states.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tariff_rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tariff_rules.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    rerank_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────
    transaction_state: Mapped["TransactionState"] = relationship(
        back_populates="rag_results",
    )

    def __repr__(self) -> str:
        return f"<RAGResult id={self.id!s} score={self.similarity_score:.4f}>"


class ExtractionResult(Base):
    """Structured fields extracted from an invoice by the LLM pipeline."""

    __tablename__ = "extraction_results"

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
    extractor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    raw_response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    token_usage: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────
    invoice: Mapped["Invoice"] = relationship(back_populates="extraction_results")

    __table_args__ = (
        Index("ix_extraction_invoice_extractor", "invoice_id", "extractor_name"),
    )

    def __repr__(self) -> str:
        return f"<ExtractionResult extractor={self.extractor_name!r}>"
