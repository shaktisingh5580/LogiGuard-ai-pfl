"""OCR Engine for invoice text extraction.

Wraps pytesseract for text extraction from PDF invoices.  Converts each PDF
page to an image via ``pdf2image``, then runs Tesseract OCR with bounding-box
output.  When Tesseract is not installed (common in hackathon / CI
environments), the engine falls back to a deterministic mock that returns
realistic sample invoice data.

Typical usage::

    engine = OCREngine(settings)
    result = await engine.extract_from_pdf("/data/invoice.pdf")
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)


# ── Data Structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned bounding box for a detected text region.

    Coordinates are in pixels relative to the page image.
    """

    x: int
    y: int
    width: int
    height: int
    text: str
    confidence: float
    page: int


@dataclass(slots=True)
class RawExtraction:
    """Result of OCR extraction from a single document.

    Attributes:
        text: Full concatenated OCR text across all pages.
        bounding_boxes: Per-word / per-cell bounding boxes with confidence.
        confidence: Weighted-average OCR confidence across all boxes.
        page_images: In-memory PIL Image objects for each page (used by VLM
            fallback when SIV detects structural issues).
        page_count: Number of pages extracted.
        source_path: Original file path that was processed.
        is_mock: True when the extraction was produced by the demo fallback.
    """

    text: str
    bounding_boxes: list[BoundingBox] = field(default_factory=list)
    confidence: float = 0.0
    page_images: list[Image.Image] = field(default_factory=list)
    page_count: int = 0
    source_path: str = ""
    is_mock: bool = False


# ── Mock Data for Hackathon ──────────────────────────────────────────────────

_MOCK_INVOICE_TEXT = """COMMERCIAL INVOICE
Invoice No: INV-2024-00198
Date: 2024-06-15
Seller: Precision Electronics Co., Ltd.
Buyer: TechHub Imports Pvt. Ltd., Mumbai, India

Country of Origin: China

| S.No | Description                          | Qty | Unit  | Unit Price (USD) | Total (USD) |
|------|--------------------------------------|-----|-------|------------------|-------------|
| 1    | Ceramic Capacitor 100nF 50V X7R     | 5000| Pcs   | 0.02             | 100.00      |
| 2    | SMD Resistor 10K Ohm 0805 1%        |10000| Pcs   | 0.005            | 50.00       |
| 3    | Linear Voltage Regulator LM7805     | 500 | Pcs   | 0.35             | 175.00      |
| 4    | Aluminum Electrolytic Cap 470uF 25V | 2000| Pcs   | 0.08             | 160.00      |
| 5    | Crystal Oscillator 16MHz HC49       | 1000| Pcs   | 0.15             | 150.00      |

Subtotal: 635.00 USD
Freight: 45.00 USD
Insurance: 12.00 USD
Total FOB: 692.00 USD

Incoterm: FOB Shanghai
Payment: T/T 30 days
HS Reference: 8532.24, 8533.21, 8541.30, 8532.22, 8541.60
"""


def _generate_mock_boxes() -> list[BoundingBox]:
    """Generate plausible bounding boxes for mock invoice data."""
    boxes: list[BoundingBox] = []
    y_offset = 200  # Header region offset
    items = [
        ("Ceramic Capacitor 100nF 50V X7R", "5000", "0.02", "100.00"),
        ("SMD Resistor 10K Ohm 0805 1%", "10000", "0.005", "50.00"),
        ("Linear Voltage Regulator LM7805", "500", "0.35", "175.00"),
        ("Aluminum Electrolytic Cap 470uF 25V", "2000", "0.08", "160.00"),
        ("Crystal Oscillator 16MHz HC49", "1000", "0.15", "150.00"),
    ]
    for row_idx, (desc, qty, price, total) in enumerate(items):
        row_y = y_offset + row_idx * 40
        # Description column
        boxes.append(BoundingBox(x=50, y=row_y, width=350, height=30, text=desc, confidence=92.5, page=0))
        # Quantity column
        boxes.append(BoundingBox(x=420, y=row_y, width=60, height=30, text=qty, confidence=95.0, page=0))
        # Unit Price column
        boxes.append(BoundingBox(x=500, y=row_y, width=80, height=30, text=price, confidence=93.0, page=0))
        # Total column
        boxes.append(BoundingBox(x=600, y=row_y, width=80, height=30, text=total, confidence=94.0, page=0))
    return boxes


def _build_mock_extraction(source_path: str) -> RawExtraction:
    """Build a complete mock extraction for demo purposes."""
    boxes = _generate_mock_boxes()
    avg_conf = sum(b.confidence for b in boxes) / len(boxes) if boxes else 0.0
    # Create a small blank image as placeholder
    placeholder = Image.new("RGB", (800, 600), color=(255, 255, 255))
    return RawExtraction(
        text=_MOCK_INVOICE_TEXT,
        bounding_boxes=boxes,
        confidence=avg_conf,
        page_images=[placeholder],
        page_count=1,
        source_path=source_path,
        is_mock=True,
    )


# ── OCR Engine ───────────────────────────────────────────────────────────────


class OCREngine:
    """Tesseract-based OCR engine for invoice PDF extraction.

    Falls back to a deterministic mock when Tesseract is unavailable, allowing
    the full pipeline to be exercised during demos and testing.

    Args:
        settings: Application settings providing ``TESSERACT_CMD`` and
            ``POPPLER_PATH`` configuration.
    """

    def __init__(self, settings: Settings) -> None:
        self._tesseract_cmd = settings.TESSERACT_CMD
        self._poppler_path = settings.POPPLER_PATH or None
        self._tesseract_available: bool | None = None

    async def _check_tesseract(self) -> bool:
        """Check if tesseract binary is available on the system."""
        if self._tesseract_available is not None:
            return self._tesseract_available

        try:
            import pytesseract  # noqa: F811

            pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd
            # Quick version check — runs in thread pool to avoid blocking
            await asyncio.to_thread(pytesseract.get_tesseract_version)
            self._tesseract_available = True
            logger.info("Tesseract OCR engine available")
        except Exception:
            self._tesseract_available = False
            logger.warning(
                "Tesseract not available — using mock OCR data. "
                "Install Tesseract for production use."
            )
        return self._tesseract_available

    async def extract_from_pdf(self, file_path: str) -> RawExtraction:
        """Extract text and bounding boxes from a PDF invoice.

        Converts each page of the PDF to a high-DPI image, runs Tesseract OCR
        with detailed output (including word-level bounding boxes and
        confidence scores), and aggregates results.

        Args:
            file_path: Absolute or relative path to the PDF file.

        Returns:
            ``RawExtraction`` containing OCR text, bounding boxes, per-word
            confidence scores, and page images.

        Raises:
            FileNotFoundError: When *file_path* does not exist.
            RuntimeError: When PDF-to-image conversion fails.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Invoice PDF not found: {file_path}")

        if not await self._check_tesseract():
            logger.info("Returning mock extraction for %s", file_path)
            return _build_mock_extraction(file_path)

        return await asyncio.to_thread(self._extract_sync, str(path))

    def _extract_sync(self, file_path: str) -> RawExtraction:
        """Synchronous extraction — run inside thread pool."""
        import pytesseract
        from pdf2image import convert_from_path

        pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd

        poppler_kwargs: dict[str, Any] = {}
        if self._poppler_path:
            poppler_kwargs["poppler_path"] = self._poppler_path

        # Convert PDF pages to 300 DPI images for optimal OCR accuracy
        try:
            images: list[Image.Image] = convert_from_path(
                file_path, dpi=300, fmt="png", **poppler_kwargs
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to convert PDF to images: {exc}"
            ) from exc

        all_text_parts: list[str] = []
        all_boxes: list[BoundingBox] = []

        for page_idx, page_img in enumerate(images):
            # Full OCR text
            page_text: str = pytesseract.image_to_string(page_img, lang="eng")
            all_text_parts.append(page_text)

            # Detailed word-level output with bounding boxes
            data: dict[str, list[Any]] = pytesseract.image_to_data(
                page_img, lang="eng", output_type=pytesseract.Output.DICT
            )

            n_words = len(data["text"])
            for i in range(n_words):
                word = data["text"][i].strip()
                conf = float(data["conf"][i])
                if not word or conf < 0:
                    continue
                all_boxes.append(
                    BoundingBox(
                        x=int(data["left"][i]),
                        y=int(data["top"][i]),
                        width=int(data["width"][i]),
                        height=int(data["height"][i]),
                        text=word,
                        confidence=conf,
                        page=page_idx,
                    )
                )

        full_text = "\n\n".join(all_text_parts)
        avg_conf = (
            sum(b.confidence for b in all_boxes) / len(all_boxes)
            if all_boxes
            else 0.0
        )

        return RawExtraction(
            text=full_text,
            bounding_boxes=all_boxes,
            confidence=avg_conf,
            page_images=images,
            page_count=len(images),
            source_path=file_path,
            is_mock=False,
        )

    async def extract_from_image(self, image: Image.Image, page_idx: int = 0) -> RawExtraction:
        """Extract text from a single PIL Image (useful for re-processing).

        Args:
            image: A PIL Image to OCR.
            page_idx: Page index to assign to bounding boxes.

        Returns:
            ``RawExtraction`` with results from the single image.
        """
        if not await self._check_tesseract():
            return _build_mock_extraction(f"<image-page-{page_idx}>")

        return await asyncio.to_thread(self._extract_image_sync, image, page_idx)

    def _extract_image_sync(self, image: Image.Image, page_idx: int) -> RawExtraction:
        """Synchronous single-image extraction."""
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd

        page_text: str = pytesseract.image_to_string(image, lang="eng")
        data: dict[str, list[Any]] = pytesseract.image_to_data(
            image, lang="eng", output_type=pytesseract.Output.DICT
        )

        boxes: list[BoundingBox] = []
        n_words = len(data["text"])
        for i in range(n_words):
            word = data["text"][i].strip()
            conf = float(data["conf"][i])
            if not word or conf < 0:
                continue
            boxes.append(
                BoundingBox(
                    x=int(data["left"][i]),
                    y=int(data["top"][i]),
                    width=int(data["width"][i]),
                    height=int(data["height"][i]),
                    text=word,
                    confidence=conf,
                    page=page_idx,
                )
            )

        avg_conf = sum(b.confidence for b in boxes) / len(boxes) if boxes else 0.0
        return RawExtraction(
            text=page_text,
            bounding_boxes=boxes,
            confidence=avg_conf,
            page_images=[image],
            page_count=1,
            source_path=f"<image-page-{page_idx}>",
            is_mock=False,
        )
