"""Statutory Field Mapper — HS code → duty rates, exemptions, PGA flags.

This is the most underestimated component. Classification is only half the
battle — mapping to gateway-specific filing fields is where real-world errors occur.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class DutyRateEntry:
    """A single duty rate for an HS code."""
    duty_type: str       # BCD, IGST, SWS, AD_DUTY, CVD
    rate: float
    rate_type: str       # PERCENT, SPECIFIC, MIXED
    notification: str | None = None


@dataclass
class ExemptionEntry:
    """An applicable exemption notification."""
    notification_number: str
    description: str
    effective_from: str
    conditions: str | None = None


@dataclass
class StatutoryFields:
    """All statutory fields required for a customs filing."""
    hs_code: str
    jurisdiction: str
    duty_rates: list[DutyRateEntry] = field(default_factory=list)
    exemptions: list[ExemptionEntry] = field(default_factory=list)
    pga_flags: list[str] = field(default_factory=list)  # BIS, FSSAI, etc.
    total_duty_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hs_code": self.hs_code,
            "jurisdiction": self.jurisdiction,
            "duty_rates": [
                {"type": d.duty_type, "rate": d.rate, "rate_type": d.rate_type}
                for d in self.duty_rates
            ],
            "exemptions": [
                {"notification": e.notification_number, "description": e.description}
                for e in self.exemptions
            ],
            "pga_flags": self.pga_flags,
            "total_duty_percent": self.total_duty_percent,
        }


class StatutoryFieldMapper:
    """Maps approved HS codes to all required statutory fields for filing."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def map(
        self,
        hs_code: str,
        jurisdiction: str = "IN",
        country_of_origin: str | None = None,
        invoice_date: date | None = None,
    ) -> StatutoryFields:
        """Map an HS code to duty rates, exemptions, and PGA flags.

        Args:
            hs_code: Approved HS classification code.
            jurisdiction: 'IN' for India, 'US' for US.
            country_of_origin: ISO country code (for origin-specific duties).
            invoice_date: For temporal accuracy.

        Returns:
            StatutoryFields with all required filing data.
        """
        fields = StatutoryFields(hs_code=hs_code, jurisdiction=jurisdiction)

        # 1. Lookup duty rates
        fields.duty_rates = await self._lookup_duty_rates(
            hs_code, jurisdiction, country_of_origin
        )

        # Calculate total duty
        fields.total_duty_percent = sum(
            d.rate for d in fields.duty_rates if d.rate_type == "PERCENT"
        )

        # 2. Match exemption notifications
        fields.exemptions = await self._match_exemptions(
            hs_code, jurisdiction, country_of_origin
        )

        # 3. Resolve PGA flags
        fields.pga_flags = await self._resolve_pga_flags(hs_code, jurisdiction)

        logger.info(
            "Statutory mapping: %s → %d duty rates, %d exemptions, PGA: %s",
            hs_code, len(fields.duty_rates), len(fields.exemptions), fields.pga_flags,
        )
        return fields

    async def _lookup_duty_rates(
        self, hs_code: str, jurisdiction: str, country_of_origin: str | None
    ) -> list[DutyRateEntry]:
        """Lookup applicable duty rates from the duty_rates table."""
        try:
            result = await self._db.execute(
                text("""
                    SELECT duty_type, rate, rate_type, notification_number
                    FROM duty_rates
                    WHERE hs_code = :code
                      AND jurisdiction = :jurisdiction
                      AND (country_of_origin IS NULL OR country_of_origin = :origin)
                      AND (effective_until IS NULL OR effective_until >= CURRENT_DATE)
                    ORDER BY duty_type
                """),
                {
                    "code": hs_code,
                    "jurisdiction": jurisdiction,
                    "origin": country_of_origin,
                },
            )
            return [
                DutyRateEntry(
                    duty_type=r[0],
                    rate=float(r[1]) if r[1] else 0.0,
                    rate_type=r[2],
                    notification=r[3],
                )
                for r in result.fetchall()
            ]
        except Exception as e:
            logger.warning("Duty rate lookup failed for %s: %s", hs_code, e)
            return []

    async def _match_exemptions(
        self, hs_code: str, jurisdiction: str, country_of_origin: str | None
    ) -> list[ExemptionEntry]:
        """Match applicable exemption notifications."""
        # For the demo: return empty. In production, this queries an exemptions table.
        return []

    async def _resolve_pga_flags(
        self, hs_code: str, jurisdiction: str
    ) -> list[str]:
        """Resolve Partner Government Agency requirements.

        For India: BIS (standards), FSSAI (food safety), CDSCO (drugs),
                   PQIS (plant quarantine), AQIS (animal quarantine)
        For US: FDA, EPA, FCC, USDA, ATF
        """
        # Known PGA mappings by chapter (simplified for demo)
        chapter = hs_code[:2] if len(hs_code) >= 2 else ""

        pga_map_india: dict[str, list[str]] = {
            "09": ["FSSAI"],           # Coffee, tea, spices
            "15": ["FSSAI"],           # Fats and oils
            "17": ["FSSAI"],           # Sugars
            "19": ["FSSAI"],           # Food preparations
            "21": ["FSSAI"],           # Miscellaneous food
            "30": ["CDSCO"],           # Pharmaceutical products
            "38": ["BIS"],             # Chemical products
            "39": ["BIS"],             # Plastics
            "73": ["BIS"],             # Iron/steel articles
            "76": ["BIS"],             # Aluminium articles
            "84": ["BIS"],             # Machinery
            "85": ["BIS", "WPC"],      # Electronics (wireless planning)
        }

        if jurisdiction == "IN":
            return pga_map_india.get(chapter, [])
        return []
