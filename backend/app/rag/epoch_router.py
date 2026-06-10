"""Epoch Router — routes queries to temporally correct vector collections.

Tariff schedules change over time. This router ensures every query
hits the legally correct data for the invoice date.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class TariffEpochNotFound(Exception):
    """No tariff epoch covers the given date and jurisdiction."""
    def __init__(self, jurisdiction: str, invoice_date: date):
        self.jurisdiction = jurisdiction
        self.invoice_date = invoice_date
        super().__init__(
            f"No tariff epoch found for jurisdiction={jurisdiction}, "
            f"date={invoice_date}. Ensure tariff data is seeded."
        )


class EpochRouter:
    """Routes vector queries to the correct epoch-partitioned collection."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_collection(self, jurisdiction: str, invoice_date: date) -> str:
        """Return the vector collection name for the legally correct tariff epoch.

        Args:
            jurisdiction: 'IN', 'US', etc.
            invoice_date: Date from the commercial invoice.

        Returns:
            Name of the vector collection to query.

        Raises:
            TariffEpochNotFound: If no epoch covers this date/jurisdiction.
        """
        result = await self._db.execute(
            text("""
                SELECT vector_collection_name, epoch_name
                FROM tariff_epochs
                WHERE jurisdiction = :jurisdiction
                  AND effective_from <= :invoice_date
                  AND (effective_until IS NULL OR effective_until >= :invoice_date)
                LIMIT 1
            """),
            {"jurisdiction": jurisdiction, "invoice_date": invoice_date},
        )
        row = result.fetchone()
        if not row:
            raise TariffEpochNotFound(jurisdiction, invoice_date)

        logger.info(
            "Epoch routed: %s/%s → %s (%s)",
            jurisdiction, invoice_date, row[0], row[1]
        )
        return row[0]

    async def list_epochs(self, jurisdiction: str) -> list[dict]:
        """List all available epochs for a jurisdiction."""
        result = await self._db.execute(
            text("""
                SELECT epoch_name, effective_from, effective_until, vector_collection_name
                FROM tariff_epochs
                WHERE jurisdiction = :jurisdiction
                ORDER BY effective_from DESC
            """),
            {"jurisdiction": jurisdiction},
        )
        return [
            {
                "epoch_name": r[0],
                "effective_from": str(r[1]),
                "effective_until": str(r[2]) if r[2] else "current",
                "collection": r[3],
            }
            for r in result.fetchall()
        ]
