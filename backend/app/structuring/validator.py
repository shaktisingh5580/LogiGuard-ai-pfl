"""Line-Item Validator and Decomposer — Layer 2: Structuring.

Takes the raw OCR / VLM extraction and decomposes it into a list of validated,
normalised ``ValidatedLineItem`` Pydantic models.  Handles:

- Required-field enforcement (description and quantity are mandatory).
- Best-effort type coercion (strings → numbers, currency symbol stripping).
- Currency normalisation (symbols → 3-letter ISO codes).
- Country-of-origin extraction from free-text.

Invalid items are returned alongside valid items so that downstream layers
can decide how to handle them (e.g. surface to HITL).

Usage::

    validator = LineItemValidator()
    valid, invalid = await validator.validate_and_decompose(raw_extraction)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.extraction.ocr import RawExtraction

logger = logging.getLogger(__name__)


# ── Currency Mapping ─────────────────────────────────────────────────────────

_CURRENCY_SYMBOL_MAP: dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₩": "KRW",
    "₽": "RUB",
    "₺": "TRY",
    "R$": "BRL",
    "A$": "AUD",
    "C$": "CAD",
    "S$": "SGD",
    "HK$": "HKD",
}

_CURRENCY_WORD_MAP: dict[str, str] = {
    "USD": "USD", "DOLLAR": "USD", "DOLLARS": "USD",
    "EUR": "EUR", "EURO": "EUR", "EUROS": "EUR",
    "GBP": "GBP", "POUND": "GBP", "POUNDS": "GBP",
    "INR": "INR", "RUPEE": "INR", "RUPEES": "INR",
    "JPY": "JPY", "YEN": "JPY",
    "CNY": "CNY", "YUAN": "CNY", "RMB": "CNY",
}


def _detect_currency(text: str) -> str:
    """Detect currency from text containing symbols or codes."""
    # Check symbols (longest first to match 'HK$' before '$')
    for symbol in sorted(_CURRENCY_SYMBOL_MAP, key=len, reverse=True):
        if symbol in text:
            return _CURRENCY_SYMBOL_MAP[symbol]
    # Check words
    for word in text.upper().split():
        word_clean = re.sub(r"[^A-Z]", "", word)
        if word_clean in _CURRENCY_WORD_MAP:
            return _CURRENCY_WORD_MAP[word_clean]
    return "USD"  # Default


def _parse_number(value: str | float | int | None) -> float | None:
    """Best-effort numeric extraction with currency-symbol stripping."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _clean_description(desc: str) -> str:
    """Normalise a product description string."""
    # Remove leading serial numbers if present (e.g. "1. " or "1) ")
    cleaned = re.sub(r"^\d+[\.\)]\s*", "", desc.strip())
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


# ── Country Extraction ───────────────────────────────────────────────────────

_COUNTRY_PATTERNS = [
    re.compile(r"country\s+of\s+origin\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"origin\s*[:\-]?\s*(.+)", re.IGNORECASE),
    re.compile(r"made\s+in\s+(.+)", re.IGNORECASE),
]


def _extract_country_of_origin(text: str) -> str | None:
    """Extract country of origin from free-text invoice content."""
    for pattern in _COUNTRY_PATTERNS:
        match = pattern.search(text)
        if match:
            country = match.group(1).strip().split("\n")[0].strip()
            # Remove trailing punctuation / noise
            country = re.sub(r"[,;.\s]+$", "", country)
            if len(country) >= 2:
                return country.title()
    return None


# ── Pydantic Models ──────────────────────────────────────────────────────────


class ValidatedLineItem(BaseModel):
    """A single validated and normalised invoice line item.

    All numeric fields have been coerced to floats, currency symbols stripped,
    and descriptions cleaned.
    """

    description: str = Field(
        ...,
        min_length=2,
        description="Full product description, cleaned and normalised.",
    )
    quantity: float = Field(
        ..., gt=0, description="Item quantity (must be positive)."
    )
    unit: str = Field(
        default="PCS",
        description="Unit of measure (e.g. PCS, KG, MT, LTR).",
    )
    unit_price: float | None = Field(
        default=None,
        ge=0,
        description="Per-unit price in the invoice currency.",
    )
    total: float | None = Field(
        default=None,
        ge=0,
        description="Line total in the invoice currency.",
    )
    country_of_origin: str | None = Field(
        default=None,
        description="Country of origin for this item.",
    )
    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="3-letter ISO 4217 currency code.",
    )
    serial_number: str | None = Field(
        default=None,
        description="Original serial / line number from the invoice.",
    )
    raw_text: str = Field(
        default="",
        description="Original unprocessed text for this line item.",
    )

    @field_validator("unit", mode="before")
    @classmethod
    def _normalise_unit(cls, v: Any) -> str:
        """Normalise unit abbreviations to uppercase standard forms."""
        if not v:
            return "PCS"
        normalised = str(v).strip().upper()
        # Common normalisations
        unit_map = {
            "PIECES": "PCS", "PIECE": "PCS", "PC": "PCS", "EA": "PCS",
            "EACH": "PCS", "NOS": "PCS", "NO": "PCS", "NOS.": "PCS",
            "KILOGRAM": "KG", "KILOGRAMS": "KG", "KGS": "KG",
            "METRIC TON": "MT", "METRIC TONS": "MT", "TONNE": "MT",
            "LITRE": "LTR", "LITER": "LTR", "LITRES": "LTR", "LITERS": "LTR", "L": "LTR",
            "METRE": "MTR", "METER": "MTR", "METRES": "MTR", "METERS": "MTR", "M": "MTR",
            "SQUARE METRE": "SQM", "SQ M": "SQM", "SQ.M": "SQM",
            "SET": "SET", "SETS": "SET",
            "PAIR": "PRS", "PAIRS": "PRS",
            "BOX": "BOX", "BOXES": "BOX",
            "CARTON": "CTN", "CARTONS": "CTN",
            "ROLL": "ROL", "ROLLS": "ROL",
        }
        return unit_map.get(normalised, normalised)


class InvalidLineItem(BaseModel):
    """Represents a line item that failed validation."""

    raw_text: str
    row_index: int
    errors: list[str]


class DecompositionResult(BaseModel):
    """Result of the validate-and-decompose operation."""

    valid_items: list[ValidatedLineItem]
    invalid_items: list[InvalidLineItem]
    detected_currency: str
    country_of_origin: str | None


# ── Validator Engine ─────────────────────────────────────────────────────────


class LineItemValidator:
    """Validates and decomposes raw OCR extractions into structured line items.

    Stateless — instantiate once and reuse across documents.
    """

    async def validate_and_decompose(
        self, raw: RawExtraction
    ) -> tuple[list[ValidatedLineItem], list[InvalidLineItem]]:
        """Parse raw extraction text into validated line items.

        Extracts table rows from the OCR text, validates each against
        business rules, coerces types, and splits results into valid and
        invalid buckets.

        Args:
            raw: The ``RawExtraction`` from OCR or VLM.

        Returns:
            Tuple of ``(valid_items, invalid_items)``.
        """
        currency = _detect_currency(raw.text)
        country = _extract_country_of_origin(raw.text)

        table_rows = self._extract_table_rows(raw.text)
        valid_items: list[ValidatedLineItem] = []
        invalid_items: list[InvalidLineItem] = []

        for row_idx, row in enumerate(table_rows):
            try:
                item = self._process_row(row, row_idx, currency, country)
                if item is not None:
                    valid_items.append(item)
            except Exception as exc:
                invalid_items.append(
                    InvalidLineItem(
                        raw_text=" | ".join(row),
                        row_index=row_idx,
                        errors=[str(exc)],
                    )
                )

        logger.info(
            "Decomposition: %d valid, %d invalid line items "
            "(currency=%s, origin=%s)",
            len(valid_items),
            len(invalid_items),
            currency,
            country,
        )

        return valid_items, invalid_items

    def _extract_table_rows(self, text: str) -> list[list[str]]:
        """Extract data rows from OCR text, skipping headers and separators."""
        rows: list[list[str]] = []
        header_found = False

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Detect pipe-delimited tables
            if "|" in stripped:
                cells = [c.strip() for c in stripped.split("|") if c.strip()]
                if not cells:
                    continue
                # Skip separator rows (e.g. |------|------|)
                if all(set(c) <= {"-", ":", " "} for c in cells):
                    continue
                # Detect header row
                if not header_found and self._is_header_row(cells):
                    header_found = True
                    continue
                if header_found and len(cells) >= 3:
                    rows.append(cells)
            # Tab-delimited fallback
            elif "\t" in stripped:
                cells = [c.strip() for c in stripped.split("\t") if c.strip()]
                if not header_found and self._is_header_row(cells):
                    header_found = True
                    continue
                if header_found and len(cells) >= 3:
                    rows.append(cells)

        return rows

    @staticmethod
    def _is_header_row(cells: list[str]) -> bool:
        """Heuristic check if a row looks like a table header."""
        header_keywords = {
            "s.no", "sno", "sr", "serial", "description", "desc", "qty",
            "quantity", "unit", "price", "total", "amount", "rate",
        }
        cell_words = {c.lower().strip().rstrip(".") for c in cells}
        return len(cell_words & header_keywords) >= 2

    def _process_row(
        self,
        row: list[str],
        row_idx: int,
        currency: str,
        country: str | None,
    ) -> ValidatedLineItem | None:
        """Process a single table row into a ValidatedLineItem.

        Expected column layout (flexible):
        [serial, description, qty, unit, unit_price, total]

        Handles both 6-column and narrower layouts gracefully.
        """
        if len(row) < 3:
            return None

        # Determine column layout based on count
        if len(row) >= 6:
            serial = row[0]
            description = row[1]
            qty_str = row[2]
            unit_str = row[3]
            price_str = row[4]
            total_str = row[5]
        elif len(row) == 5:
            serial = row[0]
            description = row[1]
            qty_str = row[2]
            unit_str = ""
            price_str = row[3]
            total_str = row[4]
        elif len(row) == 4:
            serial = ""
            description = row[0]
            qty_str = row[1]
            price_str = row[2]
            total_str = row[3]
            unit_str = ""
        else:  # 3 columns
            serial = ""
            description = row[0]
            qty_str = row[1]
            total_str = row[2]
            price_str = ""
            unit_str = ""

        # Parse numeric values
        qty = _parse_number(qty_str)
        unit_price = _parse_number(price_str)
        total = _parse_number(total_str)

        # Clean description
        desc_clean = _clean_description(description)

        # Skip rows that are clearly not data (e.g. subtotal rows)
        skip_keywords = {"subtotal", "total", "freight", "insurance", "grand total", "fob", "cif"}
        if desc_clean.lower().strip() in skip_keywords:
            return None

        # Validation: description and quantity are required
        errors: list[str] = []
        if not desc_clean or len(desc_clean) < 2:
            errors.append("Description is empty or too short")
        if qty is None or qty <= 0:
            errors.append(f"Invalid quantity: {qty_str!r}")

        if errors:
            raise ValueError("; ".join(errors))

        # Auto-compute total if missing
        if total is None and unit_price is not None and qty is not None:
            total = qty * unit_price

        # Auto-compute unit price if missing
        if unit_price is None and total is not None and qty is not None and qty > 0:
            unit_price = total / qty

        return ValidatedLineItem(
            description=desc_clean,
            quantity=qty,  # type: ignore[arg-type]  # validated above
            unit=unit_str or "PCS",
            unit_price=unit_price,
            total=total,
            country_of_origin=country,
            currency=currency,
            serial_number=serial or None,
            raw_text=" | ".join(row),
        )
