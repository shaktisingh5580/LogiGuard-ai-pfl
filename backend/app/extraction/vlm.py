"""VLM (Vision-Language Model) Fallback Extractor.

When the Structural Integrity Validator detects CRITICAL violations in the
OCR output (e.g. bounding-box overlaps, arithmetic inconsistencies), this
module re-processes the page images through GPT-4o Vision for higher-accuracy
structured table extraction.

This module is the *expensive* path — it should only be triggered when
deterministic SIV rules indicate the OCR result is unreliable.

Usage::

    vlm = VLMExtractor(openai_client, settings)
    result = await vlm.extract_from_image(page_image_bytes)
"""

from __future__ import annotations

import base64
import io
import json
import logging
from typing import TYPE_CHECKING, Any

from PIL import Image

from app.extraction.ocr import BoundingBox, RawExtraction

if TYPE_CHECKING:
    from openai import AsyncOpenAI

    from app.config import Settings

logger = logging.getLogger(__name__)

# ── Structured Output Prompt ─────────────────────────────────────────────────

_VLM_EXTRACTION_SYSTEM_PROMPT = """\
You are a precision invoice data extractor.  You will receive an image of a
commercial invoice or a page thereof.  Your task is to extract ALL tabular
line-item data into a structured JSON format.

## Output Format

Return a JSON object with exactly this schema:

```json
{
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "seller": "string or null",
  "buyer": "string or null",
  "country_of_origin": "string or null",
  "currency": "string (3-letter ISO code) or null",
  "incoterm": "string or null",
  "line_items": [
    {
      "serial": "string or int",
      "description": "string — full product description",
      "quantity": "number",
      "unit": "string (Pcs, Kg, MT, etc.)",
      "unit_price": "number",
      "total": "number"
    }
  ],
  "subtotal": "number or null",
  "freight": "number or null",
  "insurance": "number or null",
  "grand_total": "number or null"
}
```

## Rules
1. Extract EVERY line item — do not skip any row.
2. Numbers must be plain floats (no currency symbols, no commas).
3. If a cell is blank or unreadable, use ``null``.
4. Preserve the original description text verbatim.
5. If the image is unclear or partially cut off, extract what you can and
   set uncertain fields to ``null``.
6. Return ONLY the JSON — no commentary, no markdown fences.
"""


def _image_to_base64(image: Image.Image, fmt: str = "PNG") -> str:
    """Convert a PIL Image to a base64-encoded data URI string."""
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/{fmt.lower()};base64,{b64}"


def _bytes_to_base64(image_bytes: bytes) -> str:
    """Convert raw image bytes to a base64-encoded data URI string."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    # Sniff format from magic bytes
    if image_bytes[:4] == b"\x89PNG":
        mime = "image/png"
    elif image_bytes[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    else:
        mime = "image/png"
    return f"data:{mime};base64,{b64}"


class VLMExtractor:
    """GPT-4o Vision based invoice table extractor.

    This is the fallback path invoked only when the SIV detects CRITICAL
    structural violations in the OCR output.

    Args:
        client: An initialized ``AsyncOpenAI`` client.
        settings: Application settings (used for model name selection).
    """

    def __init__(self, client: AsyncOpenAI, settings: Settings) -> None:
        self._client = client
        self._model = settings.LLM_MODEL
        self._max_tokens = 4096

    async def extract_from_image(self, image_bytes: bytes) -> RawExtraction:
        """Extract structured invoice data from raw image bytes.

        Sends the image to GPT-4o Vision with a structured extraction prompt
        and parses the response into a ``RawExtraction``.

        Args:
            image_bytes: Raw bytes of the invoice page image (PNG or JPEG).

        Returns:
            ``RawExtraction`` with text reconstructed from the VLM output.
            Bounding boxes are not available from VLM — the list will be empty.
        """
        data_uri = _bytes_to_base64(image_bytes)
        return await self._call_vlm(data_uri, image_bytes)

    async def extract_from_pil_image(self, image: Image.Image) -> RawExtraction:
        """Extract structured invoice data from a PIL Image.

        Convenience method that converts the PIL Image to bytes before
        sending to the VLM.

        Args:
            image: PIL Image of the invoice page.

        Returns:
            ``RawExtraction`` with text reconstructed from the VLM output.
        """
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        image_bytes = buf.getvalue()
        data_uri = _image_to_base64(image, "PNG")
        return await self._call_vlm(data_uri, image_bytes)

    async def _call_vlm(self, data_uri: str, image_bytes: bytes) -> RawExtraction:
        """Internal: send image to VLM and parse response."""
        logger.info("VLM extraction: sending image to %s", self._model)

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _VLM_EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": data_uri, "detail": "high"},
                            },
                            {
                                "type": "text",
                                "text": "Extract all line-item data from this invoice image.",
                            },
                        ],
                    },
                ],
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
        except Exception:
            logger.exception("VLM API call failed")
            raise

        raw_content = response.choices[0].message.content or ""
        parsed = self._parse_vlm_response(raw_content)

        # Reconstruct text representation from parsed data
        text_lines: list[str] = []
        if parsed.get("invoice_number"):
            text_lines.append(f"Invoice No: {parsed['invoice_number']}")
        if parsed.get("invoice_date"):
            text_lines.append(f"Date: {parsed['invoice_date']}")
        if parsed.get("seller"):
            text_lines.append(f"Seller: {parsed['seller']}")
        if parsed.get("buyer"):
            text_lines.append(f"Buyer: {parsed['buyer']}")
        if parsed.get("country_of_origin"):
            text_lines.append(f"Country of Origin: {parsed['country_of_origin']}")

        # Reconstruct table
        if parsed.get("line_items"):
            text_lines.append("")
            text_lines.append(
                "| S.No | Description | Qty | Unit | Unit Price | Total |"
            )
            text_lines.append(
                "|------|-------------|-----|------|------------|-------|"
            )
            for item in parsed["line_items"]:
                text_lines.append(
                    f"| {item.get('serial', '')} "
                    f"| {item.get('description', '')} "
                    f"| {item.get('quantity', '')} "
                    f"| {item.get('unit', '')} "
                    f"| {item.get('unit_price', '')} "
                    f"| {item.get('total', '')} |"
                )

        if parsed.get("grand_total") is not None:
            text_lines.append(f"\nTotal: {parsed['grand_total']}")

        full_text = "\n".join(text_lines)

        # Build a page image from the original bytes
        page_image = Image.open(io.BytesIO(image_bytes))

        logger.info(
            "VLM extraction complete: %d line items extracted",
            len(parsed.get("line_items", [])),
        )

        return RawExtraction(
            text=full_text,
            bounding_boxes=[],  # VLM does not produce bounding boxes
            confidence=95.0,  # VLM outputs are generally high-confidence
            page_images=[page_image],
            page_count=1,
            source_path="<vlm-extraction>",
            is_mock=False,
        )

    @staticmethod
    def _parse_vlm_response(content: str) -> dict[str, Any]:
        """Parse the VLM JSON response, handling common formatting issues.

        The model sometimes wraps its output in markdown code fences —
        this method strips them before parsing.
        """
        cleaned = content.strip()
        # Strip markdown code fences
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("VLM response was not valid JSON — returning raw text")
            return {"line_items": [], "raw_text": content}
