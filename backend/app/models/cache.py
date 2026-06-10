"""Classification cache ORM model.

Stores recently classified descriptions with their HS code and embedding
so identical or near-identical descriptions can be resolved without a
full LLM round-trip.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClassificationCache(Base):
    """Semantic cache for classification results.

    Embeddings allow approximate nearest-neighbour lookups so that items
    with similar descriptions can bypass the full classification pipeline
    when a high-confidence prior result exists.
    """

    __tablename__ = "classification_cache"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    description_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )
    description_text: Mapped[str] = mapped_column(Text, nullable=False)
    hs_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(1536), nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_strategy: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False,
    )
    last_hit_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index(
            "ix_cache_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<ClassificationCache hs={self.hs_code!r} hits={self.hit_count}>"
