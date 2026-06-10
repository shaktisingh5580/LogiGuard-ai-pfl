"""LangGraph builder — constructs the 6-layer classification workflow.

Graph: extract → structure → classify → verify → route → [pause | complete]
"""
from __future__ import annotations

import logging
from typing import Any

from app.graph.state import PipelineState

logger = logging.getLogger(__name__)


def build_classification_graph() -> Any:
    """Build the LangGraph classification pipeline.

    Returns a compiled StateGraph that orchestrates the 6-layer pipeline.
    Uses interrupt_before for the HITL hard-pause pattern.

    NOTE: LangGraph is imported at call time to allow the rest of the
    backend to run even if langgraph isn't installed.
    """
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        logger.warning(
            "langgraph not installed. Using simplified sequential pipeline. "
            "Install with: pip install langgraph"
        )
        return None

    from app.graph.nodes import (
        extract_node,
        structure_node,
        classify_node,
        verify_node,
        route_node,
        complete_node,
    )

    # Build the graph
    workflow = StateGraph(PipelineState)

    # Add nodes
    workflow.add_node("extract", extract_node)
    workflow.add_node("structure", structure_node)
    workflow.add_node("classify", classify_node)
    workflow.add_node("verify", verify_node)
    workflow.add_node("route", route_node)
    workflow.add_node("complete", complete_node)

    # Linear flow: extract → structure → classify → verify → route
    workflow.set_entry_point("extract")
    workflow.add_edge("extract", "structure")
    workflow.add_edge("structure", "classify")
    workflow.add_edge("classify", "verify")
    workflow.add_edge("verify", "route")

    # Conditional edge from route:
    # - If needs_pause → END (wait for human review via API)
    # - If no pause needed → complete
    workflow.add_conditional_edges(
        "route",
        _route_decision,
        {
            "pause": END,       # HITL hard-pause: wait for human via REST API
            "complete": "complete",
        },
    )

    workflow.add_edge("complete", END)

    # Compile
    graph = workflow.compile()
    logger.info("Classification graph compiled successfully")
    return graph


def _route_decision(state: PipelineState) -> str:
    """Decide whether to pause for human review or proceed to completion."""
    if state.get("needs_pause", False):
        return "pause"
    return "complete"


async def run_pipeline(
    invoice_id: str,
    client_id: str,
    db_session: Any,
    events: Any = None,
) -> PipelineState:
    """Execute the full classification pipeline for an invoice.

    This is the main entry point. It either uses LangGraph (if installed)
    or falls back to sequential execution.

    Args:
        invoice_id: UUID of the uploaded invoice.
        client_id: UUID of the client.
        db_session: Async SQLAlchemy session.
        events: EventPublisher for SSE updates.

    Returns:
        Final pipeline state.
    """
    from app.graph.nodes import (
        extract_node,
        structure_node,
        classify_node,
        verify_node,
        route_node,
        complete_node,
    )

    # Initial state
    state: PipelineState = {
        "invoice_id": invoice_id,
        "client_id": client_id,
        "current_phase": "EXTRACTING",
        "line_item_ids": [],
        "current_batch_index": 0,
        "total_items": 0,
        "processed_items": 0,
        "needs_vlm": False,
        "needs_pause": False,
        "paused_item_ids": [],
        "auto_approved_ids": [],
        "error_state": None,
        "error_detail": None,
        "retry_count": 0,
    }

    # Sequential execution (LangGraph-compatible but works without it)
    node_kwargs = {"db": db_session, "events": events}

    try:
        # Layer 1: Extract
        update = await extract_node(state, **node_kwargs)
        state.update(update)

        # Layer 2: Structure
        update = await structure_node(state, **node_kwargs)
        state.update(update)

        # Layer 3: Classify
        update = await classify_node(state, **node_kwargs)
        state.update(update)

        # Layer 4: Verify
        update = await verify_node(state, **node_kwargs)
        state.update(update)

        # Layer 5: Route
        update = await route_node(state, **node_kwargs)
        state.update(update)

        # Layer 6: Complete (only if no pause needed)
        if not state.get("needs_pause", False):
            update = await complete_node(state, **node_kwargs)
            state.update(update)

    except Exception as e:
        logger.error("Pipeline failed for invoice %s: %s", invoice_id, e, exc_info=True)
        state["error_state"] = type(e).__name__
        state["error_detail"] = str(e)
        state["current_phase"] = "ERROR"

    return state
