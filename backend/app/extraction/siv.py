"""Structural Integrity Validator (SIV) for OCR output.

Applies five deterministic, non-ML rules to the raw OCR extraction to detect
table-parsing failures *before* any LLM call, saving cost and latency when
the OCR output is already clean.

Rules
-----
1. **Arithmetic consistency** – ``qty × unit_price ≈ total`` within 2 %
   tolerance.
2. **Type-shape validation** – A quantity field should never look like a
   currency value (e.g. ``$12.34``).
3. **Description contamination** – If a description cell is > 30 % numeric
   characters it likely suffered column bleed.
4. **Bounding-box overlap** – IoU > 20 % between adjacent cells signals OCR
   mis-segmentation.
5. **Column count consistency** – Every row must have the same number of
   columns as the header row.

Usage::

    siv = StructuralIntegrityValidator()
    result = siv.validate(raw_extraction)
    if result.force_vlm:
        # Fall back to VLM extraction
        ...
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.extraction.ocr import BoundingBox, RawExtraction

logger = logging.getLogger(__name__)


# ── Data Structures ──────────────────────────────────────────────────────────


class Severity(str, Enum):
    """Violation severity levels."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


@dataclass(frozen=True, slots=True)
class SIVViolation:
    """A single rule violation detected during structural validation.

    Attributes:
        rule_name: Identifier of the rule that was violated.
        severity: ``CRITICAL``, ``HIGH``, or ``MEDIUM``.
        detail: Human-readable explanation of the violation.
        affected_fields: List of field names / cell references affected.
    """

    rule_name: str
    severity: Severity
    detail: str
    affected_fields: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SIVResult:
    """Aggregate result of all SIV rule checks.

    Attributes:
        passed: ``True`` if no CRITICAL or HIGH violations were found.
        force_vlm: ``True`` if at least one CRITICAL violation requires
            VLM re-extraction.
        violations: Ordered list of all detected violations.
        rules_checked: Number of rules that were executed.
    """

    passed: bool
    force_vlm: bool
    violations: list[SIVViolation] = field(default_factory=list)
    rules_checked: int = 0


# ── Helpers ──────────────────────────────────────────────────────────────────

_CURRENCY_PATTERN = re.compile(
    r"^[\$€£¥₹]?\s*[\d,]+\.?\d*$|^\d[\d,]*\.?\d*\s*(?:USD|EUR|GBP|INR|CNY)$",
    re.IGNORECASE,
)
_NUMERIC_CHARS = re.compile(r"[\d.$€£¥₹,]")


def _parse_number(value: str) -> float | None:
    """Best-effort numeric extraction from OCR text."""
    cleaned = re.sub(r"[^\d.\-]", "", value.replace(",", ""))
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _compute_iou(a: BoundingBox, b: BoundingBox) -> float:
    """Compute Intersection-over-Union between two bounding boxes."""
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x + a.width, b.x + b.width)
    y2 = min(a.y + a.height, b.y + b.height)

    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    if intersection == 0:
        return 0.0

    area_a = a.width * a.height
    area_b = b.width * b.height
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


# ── Table Row Extraction ─────────────────────────────────────────────────────


def _extract_table_rows(text: str) -> list[list[str]]:
    """Parse pipe-delimited or whitespace-delimited table rows from OCR text.

    Returns a list of rows, where each row is a list of cell values.
    """
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        # Pipe-delimited tables (common in OCR output)
        if "|" in stripped:
            cells = [c.strip() for c in stripped.split("|") if c.strip()]
            if cells:
                rows.append(cells)
        # Fallback: tab-delimited
        elif "\t" in stripped:
            cells = [c.strip() for c in stripped.split("\t") if c.strip()]
            if len(cells) >= 3:  # At least 3 columns to be a table row
                rows.append(cells)
    return rows


# ── SIV Engine ───────────────────────────────────────────────────────────────


class StructuralIntegrityValidator:
    """Five-rule deterministic validator for OCR table extractions.

    Instantiate once and call :meth:`validate` for each document.
    All rules are side-effect-free and fully deterministic.
    """

    ARITHMETIC_TOLERANCE: float = 0.02
    """Relative tolerance for qty × unit_price ≈ total check."""

    DESCRIPTION_NUMERIC_THRESHOLD: float = 0.30
    """Fraction of numeric characters in a description cell that triggers contamination."""

    BBOX_IOU_THRESHOLD: float = 0.20
    """IoU threshold above which adjacent bounding boxes are flagged."""

    def validate(self, extracted: RawExtraction) -> SIVResult:
        """Run all five structural integrity rules against the extraction.

        Args:
            extracted: Raw OCR extraction result.

        Returns:
            ``SIVResult`` indicating pass/fail, whether VLM fallback is
            recommended, and a list of individual violations.
        """
        violations: list[SIVViolation] = []
        table_rows = _extract_table_rows(extracted.text)

        # Rule 1: Arithmetic consistency
        violations.extend(self._check_arithmetic(table_rows))

        # Rule 2: Type-shape validation
        violations.extend(self._check_type_shapes(table_rows))

        # Rule 3: Description contamination
        violations.extend(self._check_description_contamination(table_rows))

        # Rule 4: Bounding-box overlap
        violations.extend(self._check_bbox_overlap(extracted.bounding_boxes))

        # Rule 5: Column count consistency
        violations.extend(self._check_column_consistency(table_rows))

        has_critical = any(v.severity == Severity.CRITICAL for v in violations)
        has_high = any(v.severity == Severity.HIGH for v in violations)

        result = SIVResult(
            passed=not has_critical and not has_high,
            force_vlm=has_critical,
            violations=violations,
            rules_checked=5,
        )

        if violations:
            logger.info(
                "SIV found %d violations (critical=%s, force_vlm=%s)",
                len(violations),
                has_critical,
                result.force_vlm,
            )
        else:
            logger.debug("SIV: all 5 rules passed — OCR output is structurally clean")

        return result

    # ── Rule 1: Arithmetic Consistency ────────────────────────────────────

    def _check_arithmetic(self, rows: list[list[str]]) -> list[SIVViolation]:
        """Verify qty × unit_price ≈ total for each data row.

        Expects rows with at least 6 columns where indices 2, 4, 5
        correspond to Qty, Unit Price, and Total respectively (standard
        commercial invoice layout: S.No | Desc | Qty | Unit | UnitPrice | Total).
        """
        violations: list[SIVViolation] = []
        for row_idx, row in enumerate(rows):
            if len(row) < 6:
                continue
            qty = _parse_number(row[2])
            unit_price = _parse_number(row[4])
            total = _parse_number(row[5])

            if qty is None or unit_price is None or total is None:
                continue
            if total == 0:
                continue

            expected = qty * unit_price
            relative_error = abs(expected - total) / abs(total)

            if relative_error > self.ARITHMETIC_TOLERANCE:
                violations.append(
                    SIVViolation(
                        rule_name="arithmetic_consistency",
                        severity=Severity.CRITICAL,
                        detail=(
                            f"Row {row_idx}: qty({qty}) × unit_price({unit_price}) "
                            f"= {expected:.2f}, but total = {total:.2f} "
                            f"(error: {relative_error:.1%})"
                        ),
                        affected_fields=[f"row_{row_idx}_qty", f"row_{row_idx}_total"],
                    )
                )
        return violations

    # ── Rule 2: Type-Shape Validation ─────────────────────────────────────

    def _check_type_shapes(self, rows: list[list[str]]) -> list[SIVViolation]:
        """Ensure quantity fields don't look like currency values."""
        violations: list[SIVViolation] = []
        for row_idx, row in enumerate(rows):
            if len(row) < 3:
                continue
            qty_text = row[2].strip()
            if _CURRENCY_PATTERN.match(qty_text) and any(
                c in qty_text for c in "$€£¥₹"
            ):
                violations.append(
                    SIVViolation(
                        rule_name="type_shape_validation",
                        severity=Severity.HIGH,
                        detail=(
                            f"Row {row_idx}: quantity field '{qty_text}' "
                            f"looks like a currency value — possible column shift"
                        ),
                        affected_fields=[f"row_{row_idx}_qty"],
                    )
                )
        return violations

    # ── Rule 3: Description Contamination ─────────────────────────────────

    def _check_description_contamination(
        self, rows: list[list[str]]
    ) -> list[SIVViolation]:
        """Flag description cells that are predominantly numeric (column bleed)."""
        violations: list[SIVViolation] = []
        for row_idx, row in enumerate(rows):
            if len(row) < 2:
                continue
            desc = row[1]
            if not desc:
                continue
            numeric_count = len(_NUMERIC_CHARS.findall(desc))
            total_count = len(desc.replace(" ", ""))
            if total_count == 0:
                continue
            ratio = numeric_count / total_count
            if ratio > self.DESCRIPTION_NUMERIC_THRESHOLD:
                violations.append(
                    SIVViolation(
                        rule_name="description_contamination",
                        severity=Severity.HIGH,
                        detail=(
                            f"Row {row_idx}: description '{desc[:40]}...' is "
                            f"{ratio:.0%} numeric — probable column bleed"
                        ),
                        affected_fields=[f"row_{row_idx}_description"],
                    )
                )
        return violations

    # ── Rule 4: Bounding-Box Overlap ──────────────────────────────────────

    def _check_bbox_overlap(
        self, boxes: list[BoundingBox]
    ) -> list[SIVViolation]:
        """Check for excessive IoU overlap between adjacent bounding boxes."""
        violations: list[SIVViolation] = []
        if len(boxes) < 2:
            return violations

        # Group boxes by page, then check adjacent pairs in reading order
        pages: dict[int, list[BoundingBox]] = {}
        for box in boxes:
            pages.setdefault(box.page, []).append(box)

        for page_idx, page_boxes in pages.items():
            # Sort by y then x (reading order)
            sorted_boxes = sorted(page_boxes, key=lambda b: (b.y, b.x))
            for i in range(len(sorted_boxes) - 1):
                a = sorted_boxes[i]
                b = sorted_boxes[i + 1]
                iou = _compute_iou(a, b)
                if iou > self.BBOX_IOU_THRESHOLD:
                    violations.append(
                        SIVViolation(
                            rule_name="bounding_box_overlap",
                            severity=Severity.CRITICAL,
                            detail=(
                                f"Page {page_idx}: IoU={iou:.2f} between "
                                f"'{a.text}' at ({a.x},{a.y}) and "
                                f"'{b.text}' at ({b.x},{b.y}) — "
                                f"OCR mis-segmentation detected"
                            ),
                            affected_fields=[
                                f"page_{page_idx}_box_{a.text}",
                                f"page_{page_idx}_box_{b.text}",
                            ],
                        )
                    )
        return violations

    # ── Rule 5: Column Count Consistency ──────────────────────────────────

    def _check_column_consistency(
        self, rows: list[list[str]]
    ) -> list[SIVViolation]:
        """Ensure all data rows have the same column count as the header row."""
        violations: list[SIVViolation] = []
        if not rows:
            return violations

        header_cols = len(rows[0])
        for row_idx, row in enumerate(rows[1:], start=1):
            if len(row) != header_cols:
                violations.append(
                    SIVViolation(
                        rule_name="column_count_consistency",
                        severity=Severity.MEDIUM,
                        detail=(
                            f"Row {row_idx}: has {len(row)} columns but "
                            f"header has {header_cols} — possible row merge or split"
                        ),
                        affected_fields=[f"row_{row_idx}"],
                    )
                )
        return violations
