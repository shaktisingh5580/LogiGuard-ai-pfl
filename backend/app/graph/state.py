"""Lightweight LangGraph state — reference-only, never heavy data.

4KB per checkpoint instead of 10MB. Heavy data lives in DB/S3.
"""
from __future__ import annotations

from typing import TypedDict


class PipelineState(TypedDict, total=False):
    """Reference-only graph state for the classification pipeline.

    DESIGN PRINCIPLE: This state contains ONLY UUIDs, counters, and phase markers.
    Heavy data (OCR results, RAG candidates, ensemble votes) is stored in the
    database and read on-demand by each node. This keeps checkpoint size at ~4KB.
    """
    # Invoice reference
    invoice_id: str
    client_id: str

    # Pipeline progress
    current_phase: str          # EXTRACTING, STRUCTURING, CLASSIFYING, VERIFYING, ROUTING, COMPLETING
    line_item_ids: list[str]    # UUIDs of all line items
    current_batch_index: int    # For batch processing
    total_items: int
    processed_items: int

    # Decision markers
    needs_vlm: bool             # SIV failed → force VLM re-extraction
    needs_pause: bool           # Low confidence → wait for human review
    paused_item_ids: list[str]  # Items awaiting human review
    auto_approved_ids: list[str]  # Items auto-approved (high confidence)

    # Error handling
    error_state: str | None
    error_detail: str | None
    retry_count: int
