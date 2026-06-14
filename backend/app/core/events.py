"""SSE event publisher using Redis pub/sub for real-time pipeline updates."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncGenerator
from uuid import UUID

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Pipeline event types — any frontend can subscribe to these."""
    INVOICE_UPLOADED = "invoice.uploaded"
    EXTRACTION_STARTED = "extraction.started"
    EXTRACTION_COMPLETED = "extraction.completed"
    SIV_VIOLATION = "siv.violation"
    CLASSIFICATION_STARTED = "classification.started"
    CLASSIFICATION_COMPLETED = "classification.completed"
    VERIFICATION_COMPLETED = "verification.completed"
    REVIEW_REQUIRED = "review.required"
    REVIEW_COMPLETED = "review.completed"
    COMPLETION_DONE = "completion.done"
    PIPELINE_ERROR = "pipeline.error"


class PipelineEvent:
    """A single pipeline event with timestamp and payload."""

    def __init__(
        self,
        event_type: EventType,
        invoice_id: UUID | str,
        data: dict[str, Any] | None = None,
        line_item_id: UUID | str | None = None,
    ):
        self.event_type = event_type
        self.invoice_id = str(invoice_id)
        self.line_item_id = str(line_item_id) if line_item_id else None
        self.data = data or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event_type.value,
            "invoice_id": self.invoice_id,
            "line_item_id": self.line_item_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    def to_sse(self) -> str:
        """Format as Server-Sent Event string."""
        return f"event: {self.event_type.value}\ndata: {json.dumps(self.to_dict())}\n\n"


class EventPublisher:
    """Publishes pipeline events via Redis pub/sub or in-memory fallback.

    Any frontend (React, mobile, CLI) can subscribe to the SSE endpoint
    to receive real-time pipeline updates.
    """

    CHANNEL = "logiguard:events"

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client
        self._subscribers: list[asyncio.Queue[PipelineEvent]] = []

    async def publish(self, event: PipelineEvent) -> None:
        """Publish an event to all subscribers."""
        if self._redis:
            try:
                await self._redis.publish(self.CHANNEL, json.dumps(event.to_dict()))
            except Exception as e:
                logger.warning("Failed to publish event to Redis: %s", e)
        else:
            # In-memory fallback
            for queue in self._subscribers:
                queue.put_nowait(event)

        logger.info(
            "Event: %s | invoice=%s | data=%s",
            event.event_type.value,
            event.invoice_id,
            event.data,
        )

    async def subscribe(self) -> AsyncGenerator[PipelineEvent, None]:
        """Subscribe to pipeline events (for SSE endpoint)."""
        if self._redis:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(self.CHANNEL)
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        data = json.loads(message["data"])
                        yield PipelineEvent(
                            event_type=EventType(data["event"]),
                            invoice_id=data["invoice_id"],
                            data=data.get("data"),
                            line_item_id=data.get("line_item_id"),
                        )
            finally:
                await pubsub.unsubscribe(self.CHANNEL)
        else:
            # In-memory fallback
            queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()
            self._subscribers.append(queue)
            try:
                while True:
                    event = await queue.get()
                    yield event
            finally:
                self._subscribers.remove(queue)


# Singleton for the app lifetime
_publisher: EventPublisher | None = None


def get_event_publisher(redis_client: Any = None) -> EventPublisher:
    """Get or create the global event publisher."""
    global _publisher
    if _publisher is None:
        _publisher = EventPublisher(redis_client)
    return _publisher
