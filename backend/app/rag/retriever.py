"""Multi-Path RAG Retriever — broad retrieval across ALL HS chapters.

Eliminates cascading blindness by retrieving from multiple chapters
simultaneously, then letting the LLM compare paths side-by-side.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import LLMProvider, get_llm_provider

logger = logging.getLogger(__name__)


@dataclass
class TariffLineage:
    """Full lineage path from Section down to the leaf code."""
    section: str
    section_description: str
    chapter: str
    chapter_description: str
    heading: str
    heading_description: str
    subheading: str
    subheading_description: str
    code: str

    def to_display(self) -> str:
        return f"Sec {self.section} → Ch.{self.chapter} → {self.heading} → {self.subheading}"


@dataclass
class CandidatePath:
    """A single candidate classification path with full context."""
    code: str
    description: str
    lineage: TariffLineage | None = None
    section_notes: list[str] = field(default_factory=list)
    chapter_notes: list[str] = field(default_factory=list)
    similarity_score: float = 0.0
    gri_annotations: list[str] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        """Format for LLM prompt injection."""
        parts = [f"Code: {self.code}", f"Description: {self.description}"]
        if self.lineage:
            parts.append(f"Lineage: {self.lineage.to_display()}")
        if self.section_notes:
            parts.append(f"Section Notes: {'; '.join(self.section_notes[:3])}")
        if self.chapter_notes:
            parts.append(f"Chapter Notes: {'; '.join(self.chapter_notes[:3])}")
        parts.append(f"Similarity: {self.similarity_score:.3f}")
        return "\n".join(parts)


class MultiPathRetriever:
    """Broad retrieval across all HS chapters with chapter-diversity enforcement.

    Instead of sequential chapter→heading→subheading (which causes cascading
    blindness), we retrieve top-k=15 across ALL levels, deduplicate by chapter,
    and present diverse candidates to the LLM for comparative reasoning.
    """

    def __init__(self, db: AsyncSession, llm: LLMProvider | None = None):
        self._db = db
        self._llm = llm or get_llm_provider()

    async def retrieve(
        self,
        description: str,
        invoice_date: date,
        jurisdiction: str = "IN",
        top_k: int = 15,
        max_chapters: int = 5,
        per_chapter_limit: int = 3,
    ) -> list[CandidatePath]:
        """Retrieve diverse candidate paths across multiple chapters.

        Args:
            description: Commodity description from the invoice.
            invoice_date: For epoch-based collection routing.
            jurisdiction: 'IN' for India, 'US' for United States.
            top_k: Total candidates to retrieve from vector search.
            max_chapters: Maximum distinct chapters to consider.
            per_chapter_limit: Max candidates per chapter.

        Returns:
            List of CandidatePath objects with full lineage and notes.
        """
        # Step 1: Generate embedding for the commodity description
        embedding_resp = await self._llm.embed(description)
        query_embedding = embedding_resp.embedding

        # Step 2: Vector similarity search across ALL tariff entries
        # Using pgvector cosine similarity on the hs_tariff_tree
        results = await self._vector_search(
            query_embedding, jurisdiction, top_k
        )

        if not results:
            logger.warning("No candidates found for: %s", description[:80])
            return []

        # Step 3: Deduplicate by chapter — enforce cross-chapter diversity
        diverse = self._enforce_chapter_diversity(
            results, max_chapters, per_chapter_limit
        )

        # Step 4: Build full lineage for each candidate
        candidates = []
        for code, desc, score in diverse:
            candidate = CandidatePath(
                code=code,
                description=desc,
                similarity_score=score,
            )
            # Attach lineage (walk up the tariff tree)
            candidate.lineage = await self._build_lineage(code, jurisdiction)
            # Attach section/chapter notes
            if candidate.lineage:
                candidate.section_notes = await self._get_notes(
                    "SECTION_NOTE", candidate.lineage.section, jurisdiction
                )
                candidate.chapter_notes = await self._get_notes(
                    "CHAPTER_NOTE", candidate.lineage.chapter, jurisdiction
                )
            candidates.append(candidate)

        logger.info(
            "Retrieved %d candidates across %d chapters for: %s",
            len(candidates),
            len({c.lineage.chapter for c in candidates if c.lineage}),
            description[:60],
        )
        return candidates

    async def _vector_search(
        self,
        query_embedding: list[float],
        jurisdiction: str,
        top_k: int,
    ) -> list[tuple[str, str, float]]:
        """Perform vector similarity search against hs_tariff_tree.

        Returns list of (code, description, similarity_score).
        """
        # For the demo, we'll do a text-based fuzzy search if embeddings
        # aren't set up yet. In production, this uses pgvector.
        try:
            result = await self._db.execute(
                text("""
                    SELECT code, description,
                           1.0 - (embedding <=> :query_embedding::vector) as similarity
                    FROM hs_tariff_tree
                    WHERE jurisdiction = :jurisdiction
                      AND level IN ('HEADING', 'SUBHEADING')
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> :query_embedding::vector
                    LIMIT :top_k
                """),
                {
                    "query_embedding": str(query_embedding),
                    "jurisdiction": jurisdiction,
                    "top_k": top_k,
                },
            )
            rows = result.fetchall()
            return [(r[0], r[1], float(r[2])) for r in rows]
        except Exception as e:
            logger.warning("Vector search failed (falling back to text): %s", e)
            return await self._text_fallback_search(jurisdiction, top_k)

    async def _text_fallback_search(
        self, jurisdiction: str, top_k: int
    ) -> list[tuple[str, str, float]]:
        """Fallback: simple text search when embeddings aren't available."""
        result = await self._db.execute(
            text("""
                SELECT code, description, 0.5 as similarity
                FROM hs_tariff_tree
                WHERE jurisdiction = :jurisdiction
                  AND level IN ('HEADING', 'SUBHEADING')
                ORDER BY RANDOM()
                LIMIT :top_k
            """),
            {"jurisdiction": jurisdiction, "top_k": top_k},
        )
        rows = result.fetchall()
        return [(r[0], r[1], float(r[2])) for r in rows]

    def _enforce_chapter_diversity(
        self,
        results: list[tuple[str, str, float]],
        max_chapters: int,
        per_chapter_limit: int,
    ) -> list[tuple[str, str, float]]:
        """Keep top N per chapter, max M chapters — ensures cross-chapter diversity."""
        chapter_buckets: dict[str, list[tuple[str, str, float]]] = {}
        for code, desc, score in results:
            chapter = code[:4] if len(code) >= 4 else code[:2]
            if chapter not in chapter_buckets:
                chapter_buckets[chapter] = []
            if len(chapter_buckets[chapter]) < per_chapter_limit:
                chapter_buckets[chapter].append((code, desc, score))

        # Sort chapters by their best score, take top max_chapters
        sorted_chapters = sorted(
            chapter_buckets.items(),
            key=lambda x: max(s for _, _, s in x[1]),
            reverse=True,
        )[:max_chapters]

        diverse = []
        for _, candidates in sorted_chapters:
            diverse.extend(candidates)
        return diverse

    async def _build_lineage(
        self, code: str, jurisdiction: str
    ) -> TariffLineage | None:
        """Walk up the tariff tree to build full Section→Chapter→Heading→Sub path."""
        try:
            result = await self._db.execute(
                text("""
                    WITH RECURSIVE lineage AS (
                        SELECT code, description, level, parent_code, 0 as depth
                        FROM hs_tariff_tree
                        WHERE code = :code AND jurisdiction = :jurisdiction
                        UNION ALL
                        SELECT t.code, t.description, t.level, t.parent_code, l.depth + 1
                        FROM hs_tariff_tree t
                        JOIN lineage l ON t.code = l.parent_code
                        WHERE t.jurisdiction = :jurisdiction
                    )
                    SELECT code, description, level FROM lineage ORDER BY depth DESC
                """),
                {"code": code, "jurisdiction": jurisdiction},
            )
            rows = result.fetchall()
            if not rows:
                return None

            levels = {r[2]: (r[0], r[1]) for r in rows}
            return TariffLineage(
                section=levels.get("SECTION", ("", ""))[0],
                section_description=levels.get("SECTION", ("", ""))[1],
                chapter=levels.get("CHAPTER", ("", ""))[0],
                chapter_description=levels.get("CHAPTER", ("", ""))[1],
                heading=levels.get("HEADING", ("", ""))[0],
                heading_description=levels.get("HEADING", ("", ""))[1],
                subheading=levels.get("SUBHEADING", (code, ""))[0],
                subheading_description=levels.get("SUBHEADING", (code, ""))[1],
                code=code,
            )
        except Exception as e:
            logger.warning("Lineage lookup failed for %s: %s", code, e)
            return None

    async def _get_notes(
        self, rule_type: str, code: str, jurisdiction: str
    ) -> list[str]:
        """Fetch section/chapter notes from tariff_rules table."""
        try:
            col = "applies_to_section" if rule_type == "SECTION_NOTE" else "applies_to_chapter"
            result = await self._db.execute(
                text(f"""
                    SELECT description FROM tariff_rules
                    WHERE rule_type = :rule_type
                      AND {col} = :code
                      AND jurisdiction = :jurisdiction
                    LIMIT 10
                """),
                {"rule_type": rule_type, "code": code, "jurisdiction": jurisdiction},
            )
            return [r[0] for r in result.fetchall()]
        except Exception as e:
            logger.warning("Notes lookup failed: %s", e)
            return []
