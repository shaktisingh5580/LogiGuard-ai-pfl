"""Confidence Regime — tiered threshold system for cold-start handling.

New clients start in BOOTSTRAP (everything reviewed by human).
After 50 invoices → CALIBRATING. After 200 → PRODUCTION.
"""
from __future__ import annotations

import logging
from enum import Enum
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class Regime(str, Enum):
    BOOTSTRAP = "BOOTSTRAP"       # First ~50 invoices — all human review
    CALIBRATING = "CALIBRATING"   # 51-200 — learning client patterns
    PRODUCTION = "PRODUCTION"     # 200+ — full adaptive thresholds


# Chapter-level priors (some chapters are inherently ambiguous)
CHAPTER_PRIORS: dict[str, float] = {
    "84": 0.88,  # Machinery — often ambiguous
    "85": 0.88,  # Electrical — many overlaps
    "39": 0.85,  # Plastics — cross-section conflicts
    "73": 0.82,  # Iron/steel articles — specific vs general
    "90": 0.90,  # Instruments — highly technical
}
DEFAULT_CHAPTER_PRIOR = 0.80


class ConfidenceRegime:
    """Manages per-client confidence thresholds with cold-start bootstrapping."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_regime(self, client_id: UUID) -> Regime:
        """Determine the current regime for a client."""
        result = await self._db.execute(
            text("SELECT regime, total_reviewed_invoices FROM clients WHERE id = :id"),
            {"id": client_id},
        )
        row = result.fetchone()
        if not row:
            return Regime.BOOTSTRAP
        return Regime(row[0])

    async def get_threshold(self, client_id: UUID, hs_chapter: str) -> float:
        """Get the confidence threshold for a client + chapter combination.

        Below this threshold → item is flagged for human review (HARD PAUSE).
        """
        regime = await self.get_regime(client_id)

        if regime == Regime.BOOTSTRAP:
            return 1.0  # Everything goes to human review

        elif regime == Regime.CALIBRATING:
            # Use client's own override rate where available
            override_rate = await self._get_client_override_rate(client_id, hs_chapter)
            if override_rate is not None and override_rate["sample_size"] >= 10:
                rate = override_rate["rate"]
                # Higher override rate → higher threshold (more cautious)
                return min(0.95, 0.70 + rate * 0.8)
            else:
                return CHAPTER_PRIORS.get(hs_chapter[:2], DEFAULT_CHAPTER_PRIOR)

        elif regime == Regime.PRODUCTION:
            # Full adaptive — per-client, per-chapter
            override_rate = await self._get_client_override_rate(client_id, hs_chapter)
            if override_rate and override_rate["sample_size"] >= 30:
                rate = override_rate["rate"]
                return min(0.95, max(0.65, 0.70 + rate * 0.6))
            return CHAPTER_PRIORS.get(hs_chapter[:2], DEFAULT_CHAPTER_PRIOR)

        return DEFAULT_CHAPTER_PRIOR

    def should_pause(
        self,
        confidence: float,
        threshold: float,
        is_restricted: bool = False,
    ) -> bool:
        """Determine if a line item should hard-pause for human review."""
        if is_restricted:
            return True  # Always pause for restricted goods
        return confidence < threshold

    async def maybe_advance_regime(self, client_id: UUID) -> Regime | None:
        """Check if the client should advance to the next regime."""
        result = await self._db.execute(
            text("SELECT regime, total_reviewed_invoices FROM clients WHERE id = :id"),
            {"id": client_id},
        )
        row = result.fetchone()
        if not row:
            return None

        current_regime = Regime(row[0])
        reviewed = row[1]

        new_regime = None
        if current_regime == Regime.BOOTSTRAP and reviewed >= 50:
            new_regime = Regime.CALIBRATING
        elif current_regime == Regime.CALIBRATING and reviewed >= 200:
            # Check override rate before advancing
            override = await self._get_global_override_rate(client_id)
            if override and override["rate"] < 0.15:
                new_regime = Regime.PRODUCTION

        if new_regime:
            await self._db.execute(
                text("UPDATE clients SET regime = :regime WHERE id = :id"),
                {"regime": new_regime.value, "id": client_id},
            )
            await self._db.commit()
            logger.info("Client %s advanced to %s", client_id, new_regime.value)
            return new_regime

        return None

    async def _get_client_override_rate(
        self, client_id: UUID, hs_chapter: str
    ) -> dict | None:
        """Get override rate for a specific client + chapter (last 30 days)."""
        try:
            result = await self._db.execute(
                text("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN human_final_code != ai_recommended_code THEN 1 ELSE 0 END) as overrides
                    FROM transaction_states ts
                    JOIN invoices i ON ts.invoice_id = i.id
                    WHERE i.client_id = :client_id
                      AND ts.ai_recommended_code LIKE :chapter_prefix
                      AND ts.human_final_code IS NOT NULL
                      AND ts.updated_at >= NOW() - INTERVAL '30 days'
                """),
                {"client_id": client_id, "chapter_prefix": f"{hs_chapter[:2]}%"},
            )
            row = result.fetchone()
            if not row or row[0] == 0:
                return None
            return {
                "sample_size": row[0],
                "rate": row[1] / row[0] if row[0] > 0 else 0,
            }
        except Exception as e:
            logger.warning("Override rate lookup failed: %s", e)
            return None

    async def _get_global_override_rate(self, client_id: UUID) -> dict | None:
        """Get overall override rate for a client (all chapters)."""
        try:
            result = await self._db.execute(
                text("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN human_final_code != ai_recommended_code THEN 1 ELSE 0 END) as overrides
                    FROM transaction_states ts
                    JOIN invoices i ON ts.invoice_id = i.id
                    WHERE i.client_id = :client_id
                      AND ts.human_final_code IS NOT NULL
                """),
                {"client_id": client_id},
            )
            row = result.fetchone()
            if not row or row[0] == 0:
                return None
            return {"sample_size": row[0], "rate": row[1] / row[0]}
        except Exception:
            return None
