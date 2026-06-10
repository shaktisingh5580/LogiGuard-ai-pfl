"""Server-Sent Events endpoint for real-time pipeline updates."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from app.core.events import get_event_publisher

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/events")
async def stream_events(
    invoice_id: str | None = Query(None, description="Filter events for a specific invoice"),
):
    """Stream real-time pipeline events via Server-Sent Events (SSE).

    The frontend can connect to this endpoint to receive live updates
    as the classification pipeline processes an invoice.

    Usage (JavaScript):
        const es = new EventSource('/api/events?invoice_id=xxx');
        es.onmessage = (e) => console.log(JSON.parse(e.data));
    """
    async def event_generator():
        publisher = get_event_publisher()

        # Send an initial heartbeat so the client knows the connection is alive
        yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'timestamp': datetime.now(timezone.utc).isoformat()})}\n\n"

        try:
            async for event in publisher.subscribe():
                # If filtering by invoice_id, skip non-matching events
                if invoice_id and event.invoice_id != invoice_id:
                    continue
                yield event.to_sse()
        except asyncio.CancelledError:
            logger.info("SSE client disconnected")
            return
        except Exception as e:
            logger.error("SSE stream error: %s", e)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
