"""In-memory async message router with persistence via MessageStore."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from hive.bus.store import MessageStore

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """A routed message between entities."""

    sender: str
    recipient: str
    content: str
    conversation_id: str | None = None
    metadata: dict[str, Any] | None = None


class MessageRouter:
    """Routes messages between entities via async queues.

    Real-time delivery uses asyncio.Queue per entity.
    All messages are also logged to the MessageStore for persistence.
    """

    def __init__(self, store: MessageStore) -> None:
        self.store = store
        self._queues: dict[str, asyncio.Queue[Message]] = {}

    def register(self, entity_name: str) -> None:
        """Register an entity for message delivery."""
        if entity_name not in self._queues:
            self._queues[entity_name] = asyncio.Queue()
            logger.info("Registered entity: %s", entity_name)

    def unregister(self, entity_name: str) -> None:
        """Remove an entity from the router."""
        self._queues.pop(entity_name, None)
        logger.info("Unregistered entity: %s", entity_name)

    async def route(
        self,
        sender: str,
        recipient: str,
        content: str,
        conversation_id: str | None = None,
    ) -> None:
        """Route a message: log to store and deliver to recipient's queue."""
        # Log to persistent store
        await self.store.log_message(
            sender=sender,
            recipient=recipient,
            content=content,
            conversation_id=conversation_id,
        )

        # Deliver to queue if recipient is registered
        msg = Message(
            sender=sender,
            recipient=recipient,
            content=content,
            conversation_id=conversation_id,
        )
        if recipient in self._queues:
            await self._queues[recipient].put(msg)
        else:
            logger.warning("No queue for recipient %s, message logged but not delivered", recipient)

    async def get_next(self, entity_name: str, timeout: float | None = None) -> Message | None:
        """Get the next message for an entity. Blocks until available or timeout."""
        if entity_name not in self._queues:
            return None

        try:
            if timeout is not None:
                return await asyncio.wait_for(self._queues[entity_name].get(), timeout=timeout)
            return await self._queues[entity_name].get()
        except TimeoutError:
            return None

    def has_pending(self, entity_name: str) -> bool:
        """Check if an entity has pending messages."""
        if entity_name not in self._queues:
            return False
        return not self._queues[entity_name].empty()

    async def broadcast(self, sender: str, content: str) -> None:
        """Send a message to all registered entities."""
        for entity_name in self._queues:
            if entity_name != sender:
                await self.route(sender, entity_name, content)

    @property
    def registered_entities(self) -> list[str]:
        """List all registered entity names."""
        return list(self._queues.keys())
