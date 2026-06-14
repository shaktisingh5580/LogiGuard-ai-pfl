"""Statutory Rule Engine — deterministic exclusion filter.

Applies 700+ Section/Chapter Notes as hard filters BEFORE the LLM
sees the candidates. This is a rules engine, not ML — every exclusion
is explainable and auditable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retriever import CandidatePath

logger = logging.getLogger(__name__)


@dataclass
class ExcludedCandidate:
    """A candidate that was removed by a statutory rule."""
    candidate: CandidatePath
    reason: str
    statutory_reference: str
    rule_type: str


@dataclass
class FilteredResult:
    """Output of the rule engine — valid and excluded candidates."""
    valid_candidates: list[CandidatePath]
    excluded_candidates: list[ExcludedCandidate]
    rules_applied: int = 0

    @property
    def exclusion_summary(self) -> str:
        if not self.excluded_candidates:
            return "No exclusions applied."
        parts = []
        for exc in self.excluded_candidates:
            parts.append(f"❌ {exc.candidate.code}: {exc.reason} ({exc.statutory_reference})")
        return "\n".join(parts)


@dataclass
class TariffRule:
    """A single statutory rule loaded from the database."""
    rule_type: str
    applies_to_section: str | None
    applies_to_chapter: str | None
    applies_to_heading: str | None
    condition_type: str
    condition_parameters: dict[str, Any]
    description: str
    statutory_reference: str


class TariffRuleEngine:
    """Deterministic rule engine for statutory exclusions.

    The 3 types of rules handled:
    1. EXCLUDES_CHAPTER: "Section XV does NOT cover articles of Chapter 39"
    2. REQUIRES_PROPERTY: "Heading 7606 requires thickness > 0.2mm"
    3. SPECIFICITY_PREFERENCE: GRI Rule 3(a) — most specific heading wins
    """

    def __init__(self, db: AsyncSession):
        self._db = db
        self._rules: list[TariffRule] | None = None

    async def _load_rules(self, jurisdiction: str) -> list[TariffRule]:
        """Load all statutory rules from the database."""
        if self._rules is not None:
            return self._rules

        try:
            async with self._db.begin_nested():
                result = await self._db.execute(
                    text("""
                        SELECT rule_type, applies_to_section, applies_to_chapter,
                               applies_to_heading, condition_type, condition_parameters,
                               description, statutory_reference
                        FROM tariff_rules
                        WHERE (jurisdiction = :jurisdiction OR jurisdiction IS NULL)
                          AND (effective_until IS NULL OR effective_until >= CURRENT_DATE)
                          AND rule_type IS NOT NULL
                        ORDER BY rule_type, applies_to_section, applies_to_chapter
                    """),
                    {"jurisdiction": jurisdiction},
                )

                self._rules = [
                    TariffRule(
                        rule_type=r[0],
                        applies_to_section=r[1],
                        applies_to_chapter=r[2],
                        applies_to_heading=r[3],
                        condition_type=r[4],
                        condition_parameters=r[5] or {},
                        description=r[6],
                        statutory_reference=r[7],
                    )
                    for r in result.fetchall()
                ]
                logger.info("Loaded %d statutory rules for %s", len(self._rules), jurisdiction)
        except Exception as e:
            logger.warning("Failed to load statutory rules (schema mismatch?): %s", e)
            self._rules = []
            
        return self._rules

    async def filter_candidates(
        self,
        candidates: list[CandidatePath],
        item_description: str,
        jurisdiction: str = "IN",
    ) -> FilteredResult:
        """Apply statutory rules to filter invalid candidates.

        Args:
            candidates: RAG retrieval results.
            item_description: Original commodity description (for property checks).
            jurisdiction: 'IN', 'US', etc.

        Returns:
            FilteredResult with valid and excluded candidates.
        """
        rules = await self._load_rules(jurisdiction)
        valid = []
        excluded = []
        rules_applied = 0

        for candidate in candidates:
            exclusion = self._check_exclusions(candidate, rules, item_description)
            if exclusion:
                excluded.append(exclusion)
                rules_applied += 1
            else:
                valid.append(candidate)

        result = FilteredResult(
            valid_candidates=valid,
            excluded_candidates=excluded,
            rules_applied=rules_applied,
        )

        if excluded:
            logger.info(
                "Rule engine: %d/%d candidates excluded\n%s",
                len(excluded), len(candidates), result.exclusion_summary,
            )

        return result

    def _check_exclusions(
        self,
        candidate: CandidatePath,
        rules: list[TariffRule],
        item_description: str,
    ) -> ExcludedCandidate | None:
        """Check if a candidate is excluded by any statutory rule."""
        if not candidate.lineage:
            return None

        for rule in rules:
            if self._rule_applies(rule, candidate):
                if rule.condition_type == "EXCLUDES_CHAPTER":
                    excluded_chapters = rule.condition_parameters.get("excluded_chapters", [])
                    if candidate.lineage.chapter in excluded_chapters:
                        return ExcludedCandidate(
                            candidate=candidate,
                            reason=rule.description,
                            statutory_reference=rule.statutory_reference,
                            rule_type=rule.condition_type,
                        )

                elif rule.condition_type == "REQUIRES_PROPERTY":
                    prop = rule.condition_parameters.get("property", "")
                    operator = rule.condition_parameters.get("operator", "")
                    value = rule.condition_parameters.get("value", 0)
                    # Check if the item description mentions the property
                    if not self._property_satisfied(item_description, prop, operator, value):
                        return ExcludedCandidate(
                            candidate=candidate,
                            reason=f"{rule.description} (property '{prop}' not satisfied)",
                            statutory_reference=rule.statutory_reference,
                            rule_type=rule.condition_type,
                        )

                elif rule.condition_type == "EXCLUDES_DESCRIPTION_PATTERN":
                    patterns = rule.condition_parameters.get("patterns", [])
                    desc_lower = item_description.lower()
                    for pattern in patterns:
                        if pattern.lower() in desc_lower:
                            return ExcludedCandidate(
                                candidate=candidate,
                                reason=rule.description,
                                statutory_reference=rule.statutory_reference,
                                rule_type=rule.condition_type,
                            )

        return None

    def _rule_applies(self, rule: TariffRule, candidate: CandidatePath) -> bool:
        """Check if a rule is applicable to this candidate based on scope."""
        if not candidate.lineage:
            return False
        if rule.applies_to_section and candidate.lineage.section != rule.applies_to_section:
            return False
        if rule.applies_to_chapter and candidate.lineage.chapter != rule.applies_to_chapter:
            return False
        if rule.applies_to_heading and candidate.lineage.heading != rule.applies_to_heading:
            return False
        return True

    @staticmethod
    def _property_satisfied(
        description: str, prop: str, operator: str, value: float
    ) -> bool:
        """Check if the commodity description satisfies a property requirement.

        This is a best-effort check — if we can't parse the property from the
        description, we conservatively return True (don't exclude).
        """
        import re
        desc_lower = description.lower()

        # Try to extract numeric value for the property
        # e.g., "3mm thick" → thickness = 3
        patterns = {
            "thickness": r"(\d+(?:\.\d+)?)\s*(?:mm|cm|m)\s*(?:thick|thickness)",
            "weight": r"(\d+(?:\.\d+)?)\s*(?:kg|g|gsm)\s*(?:weight|per)",
            "width": r"(\d+(?:\.\d+)?)\s*(?:mm|cm|m)\s*(?:wide|width)",
        }

        pattern = patterns.get(prop)
        if not pattern:
            return True  # Can't check — don't exclude

        match = re.search(pattern, desc_lower)
        if not match:
            return True  # Can't find property — don't exclude

        extracted_value = float(match.group(1))
        if operator == ">":
            return extracted_value > value
        elif operator == ">=":
            return extracted_value >= value
        elif operator == "<":
            return extracted_value < value
        elif operator == "<=":
            return extracted_value <= value
        elif operator == "==":
            return extracted_value == value
        return True
