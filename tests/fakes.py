"""Shared test doubles for the PTY-only runtime (Ticket 007).

Before 007 the unit suite mocked ``ClaudeSession`` (the headless subprocess
wrapper) and relied on ``HIVE_USE_PTY=false`` so ``send_to_entity`` took the
subprocess branch. With the headless path gone, tests mock at the
``ClaudeAdapter`` boundary instead — the manager's natural collaborator seam,
already the injection point (``_get_or_create_adapter``).
"""

from __future__ import annotations

from contextlib import contextmanager

# Sentinel for ``FakeAdapter(responses=[...])``: a turn that raises
# ``TimeoutError`` instead of returning text — the no-progress timeout the
# real reader raises (``transcript_reader.py``). Used to drive the Ticket 020
# auto-bounce path hermetically. As with text entries, the LAST entry repeats,
# so ``responses=[TIMEOUT]`` is "always times out".
TIMEOUT = object()


class FakeAdapter:
    """PTY-shaped stand-in for ``ClaudeAdapter`` — no subprocess, no PTY.

    Mirrors the slice of the real adapter the manager/dispatcher consume:
    ``start``/``stop``/``is_alive``/``session_id`` and ``send_turn`` returning
    ``(text, usage)``. ``is_alive`` is a METHOD (the real adapter's is too —
    not the property ``ClaudeSession.is_alive`` was).

    Ticket 020 additions (all opt-in, default to the pre-020 behaviour):
    a ``TIMEOUT`` sentinel in ``responses`` scripts a no-progress timeout;
    ``workflow_active`` / ``jam_state`` back the two liveness probes the
    auto-bounce decision and reason-assembler read off the real adapter.
    """

    def __init__(
        self,
        responses: str | list = "ok",
        *,
        session_id: str = "sess-1",
        usage: dict | None = None,
        workflow_active: bool = False,
        jam_state: dict | None = None,
    ) -> None:
        # A single str = one canned turn; a list = successive turns (e.g. a
        # compaction summarise-then-reseed sequence, or a TIMEOUT script). The
        # last entry repeats.
        self._responses = responses if isinstance(responses, list) else [responses]
        self._i = 0
        self._session_id = session_id
        self._usage = usage or {}
        # Ticket 020: liveness probes the bounce logic reads.
        self._workflow_active = workflow_active
        self._jam_state = jam_state
        self.started = False
        self.stopped = False
        self.prompts: list[str] = []  # every prompt sent, for assertions

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def is_alive(self) -> bool:
        return self.started and not self.stopped

    def is_busy(self) -> bool:
        # Fake turns resolve synchronously, so a FakeAdapter is never
        # mid-turn when the idle reaper looks at it.
        return False

    def workflow_active(self, window: float) -> bool:
        """Liveness probe the bounce safety-check reads (Ticket 020 §D1)."""
        return self._workflow_active

    def describe_jam(self) -> dict | None:
        """Best-effort session-state the reason-assembler reads (Ticket 020 §D5)."""
        return self._jam_state

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        self.prompts.append(prompt)
        outcome = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        if outcome is TIMEOUT:
            raise TimeoutError("Turn did not complete within 180.0s")
        usage: dict = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "session_id": self._session_id,
            "cost_usd": None,
            **self._usage,
        }
        return outcome, usage


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


@contextmanager
def using_adapter_sequence(mgr, adapters: list[FakeAdapter]):
    """Hand out a fresh adapter from ``adapters`` on each (re)spawn.

    Models a Ticket 020 auto-bounce: ``_get_or_create_adapter`` returns the
    registered adapter while it is alive (the real lazy cache), but once the
    bounce stops it (``is_alive()`` False) — or pops it from ``_adapters`` —
    the next call provisions the next adapter in the list, exactly as the real
    lifecycle respawns a fresh PTY. Restores the original method on exit.
    """
    pending = list(adapters)
    original = mgr._get_or_create_adapter

    async def _get(entity):
        existing = mgr._adapters.get(entity.name)
        if existing is not None and existing.is_alive():
            return existing
        adapter = pending.pop(0)
        mgr._adapters[entity.name] = adapter
        if not adapter.started:
            await adapter.start()
        return adapter

    mgr._get_or_create_adapter = _get
    try:
        yield adapters
    finally:
        mgr._get_or_create_adapter = original
