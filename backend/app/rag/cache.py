"""Semantic Cache — avoids redundant LLM calls for known commodities.

If we've seen this (or nearly this) commodity description before and a human
approved it, return the cached code instead of running the full RAG pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import LLMProvider, get_llm_provider

logger = logging.getLogger(__name__)


@dataclass
class CacheHit:
    """Result from a semantic cache lookup."""
    cache_id: UUID
    canonical_description: str
    hs_code: str
    similarity: float
    approval_count: int
    override_count: int
    override_rate: float


class SemanticCache:
    """Embedding-based classification cache.

    Doubles as a client-specific knowledge base. Over time, a textile importer's
    cache becomes a highly accurate lookup table for their commodity vocabulary.
    """

    def __init__(self, db: AsyncSession, llm: LLMProvider | None = None):
        self._db = db
        self._llm = llm or get_llm_provider()

    async def lookup(
        self,
        description: str,
        jurisdiction: str = "IN",
        threshold: float = 0.95,
    ) -> Optional[CacheHit]:
        """Find a cached classification for a similar commodity description.

        Args:
            description: The commodity description to look up.
            jurisdiction: Jurisdiction code.
            threshold: Minimum cosine similarity (0.95 = very similar).

        Returns:
            CacheHit if a match is found with low override rate, else None.
        """
        embedding_resp = await self._llm.embed(description)
        query_embedding = embedding_resp.embedding

        try:
            result = await self._db.execute(
                text("""
                    SELECT id, canonical_description, hs_code,
                           1 - (description_embedding <=> :embedding::vector) as similarity,
                           approval_count, override_count
                    FROM classification_cache
                    WHERE jurisdiction = :jurisdiction
                      AND 1 - (description_embedding <=> :embedding::vector) >= :threshold
                    ORDER BY description_embedding <=> :embedding::vector
                    LIMIT 1
                """),
                {
                    "embedding": str(query_embedding),
                    "jurisdiction": jurisdiction,
                    "threshold": threshold,
                },
            )
            row = result.fetchone()
            if not row:
                return None

            total = row[4] + row[5]
            override_rate = row[5] / total if total > 0 else 0.0

            # Only return cache hit if override rate is below 5%
            if override_rate > 0.05:
                logger.info(
                    "Cache match found but override rate too high (%.1f%%): %s",
                    override_rate * 100, row[2]
                )
                return None

            hit = CacheHit(
                cache_id=row[0],
                canonical_description=row[1],
                hs_code=row[2],
                similarity=float(row[3]),
                approval_count=row[4],
                override_count=row[5],
                override_rate=override_rate,
            )
            logger.info("Cache HIT: %s → %s (sim=%.3f)", description[:50], hit.hs_code, hit.similarity)
            return hit

        except Exception as e:
            logger.warning("Cache lookup failed (proceeding without cache): %s", e)
            return None

    async def write_back(
        self,
        description: str,
        hs_code: str,
        jurisdiction: str = "IN",
        epoch_id: UUID | None = None,
    ) -> None:
        """Write a human-approved classification to the cache."""
        embedding_resp = await self._llm.embed(description)

        try:
            # Check if similar entry already exists
            existing = await self.lookup(description, jurisdiction, threshold=0.98)
            if existing:
                # Increment approval count
                await self._db.execute(
                    text("""
                        UPDATE classification_cache
                        SET approval_count = approval_count + 1,
                            last_approved_at = :now
                        WHERE id = :id
                    """),
                    {"id": existing.cache_id, "now": datetime.now(timezone.utc)},
                )
            else:
                # Insert new cache entry
                await self._db.execute(
                    text("""
                        INSERT INTO classification_cache
                            (description_embedding, canonical_description, hs_code,
                             jurisdiction, tariff_epoch_id, approval_count, last_approved_at)
                        VALUES
                            (:embedding::vector, :description, :hs_code,
                             :jurisdiction, :epoch_id, 1, :now)
                    """),
                    {
                        "embedding": str(embedding_resp.embedding),
                        "description": description,
                        "hs_code": hs_code,
                        "jurisdiction": jurisdiction,
                        "epoch_id": epoch_id,
                        "now": datetime.now(timezone.utc),
                    },
                )
            await self._db.commit()
            logger.info("Cache write-back: %s → %s", description[:50], hs_code)

        except Exception as e:
            logger.warning("Cache write-back failed: %s", e)

    async def record_override(self, cache_id: UUID) -> None:
        """Record that a human overrode a cached classification."""
        await self._db.execute(
            text("""
                UPDATE classification_cache
                SET override_count = override_count + 1,
                    last_override_at = :now
                WHERE id = :id
            """),
            {"id": cache_id, "now": datetime.now(timezone.utc)},
        )
        await self._db.commit()
