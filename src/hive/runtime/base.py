"""Harness-agnostic Runtime interface — the turn-shaped contract every adapter implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class QuotaExhausted(Exception):
    """A Turn died on a plan-quota wall, not a stall.

    At 100% plan quota the harness writes nothing, so the turn would otherwise
    dead-end in the no-progress timeout and read as a jam. Adapters raise this
    instead so callers can tell "the plan is spent until ``resets_at``" apart
    from "the session is wedged" — and must never bounce the entity for it.
    """

    def __init__(self, resets_at: datetime | None) -> None:
        self.resets_at = resets_at
        when = resets_at.strftime("%Y-%m-%d %H:%M UTC") if resets_at is not None else "unknown"
        super().__init__(f"plan quota exhausted — resets {when}")


class Runtime(ABC):
    """Adapter interface between the orchestrator and a harness (Claude Code, Codex, etc.).

    Each entity holds one Runtime instance. The orchestrator calls send_turn()
    for every prompt and reads back the assistant text plus token usage.
    Lifecycle: start() once on creation, stop() on teardown.
    """

    @abstractmethod
    async def start(self) -> None:
        """Initialise the harness process or connection."""

    @abstractmethod
    async def stop(self) -> None:
        """Cleanly shut down the harness."""

    @abstractmethod
    def is_alive(self) -> bool:
        """True if the harness is ready to accept a turn."""

    @abstractmethod
    async def send_turn(self, prompt: str) -> tuple[str, dict[str, int]]:
        """Send one turn and return (response_text, usage).

        usage keys: input_tokens, output_tokens (always present, zero on failure).
        """
