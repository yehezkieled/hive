"""Shared test doubles for the PTY-only runtime (Ticket 007).

Before 007 the unit suite mocked ``ClaudeSession`` (the headless subprocess
wrapper) and relied on ``HIVE_USE_PTY=false`` so ``send_to_entity`` took the
subprocess branch. With the headless path gone, tests mock at the
``ClaudeAdapter`` boundary instead — the manager's natural collaborator seam,
already the injection point (``_get_or_create_adapter``).
"""

from __future__ import annotations

from contextlib import contextmanager


class FakeAdapter:
    """PTY-shaped stand-in for ``ClaudeAdapter`` — no subprocess, no PTY.

    Mirrors the slice of the real adapter the manager/dispatcher consume:
    ``start``/``stop``/``is_alive``/``session_id`` and ``send_turn`` returning
    ``(text, usage)``. ``is_alive`` is a METHOD (the real adapter's is too —
    not the property ``ClaudeSession.is_alive`` was).
    """

    def __init__(
        self,
        responses: str | list[str] = "ok",
        *,
        session_id: str = "sess-1",
        usage: dict | None = None,
    ) -> None:
        # A single str = one canned turn; a list = successive turns (e.g. a
        # compaction summarise-then-reseed sequence). The last entry repeats.
        self._responses = responses if isinstance(responses, list) else [responses]
        self._i = 0
        self._session_id = session_id
        self._usage = usage or {}
        self.started = False
        self.stopped = False
        self.prompts: list[str] = []  # every prompt sent, for assertions

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def is_alive(self) -> bool:
        return self.started and not self.stopped

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        self.prompts.append(prompt)
        text = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        usage: dict = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "session_id": self._session_id,
            "cost_usd": None,
            **self._usage,
        }
        return text, usage


@contextmanager
def using_adapter(mgr, adapter: FakeAdapter):
    """Force ``mgr._get_or_create_adapter`` to return ``adapter`` for the block.

    Registers the adapter in ``mgr._adapters`` so ``kill_entity`` / ``stop_all``
    / ``active_count`` / ``get_status`` see it, exactly as the real lazy path
    would. Restores the original method on exit.
    """
    original = mgr._get_or_create_adapter

    async def _get(entity):
        mgr._adapters[entity.name] = adapter
        if not adapter.started:
            await adapter.start()
        return adapter

    mgr._get_or_create_adapter = _get
    try:
        yield adapter
    finally:
        mgr._get_or_create_adapter = original
