"""Harness registry types: what Hive can drive, how, and what went wrong.

ADR 0029. Hive drives whichever agent *harness* is installed and signed in
(Pi first, then Claude Code) and runs each turn in the harness's headless mode,
falling back to a PTY-driven interactive session only when headless is refused
or out of quota. This module is the vocabulary for that:

* ``HarnessSpec`` — one registry entry: how to probe a harness and how to build
  a ``Runtime`` for a (harness, mode). **This is the extension point.** The
  Codex (T015) and OpenCode (T016) adapters, or a direct model-API harness, are
  each one more spec in ``registry.py`` — no router or dispatcher change.
* ``HarnessStatus`` — the result of a probe (installed? signed in?).
* ``HarnessError`` — a failed run, classified from the harness's *real* error
  output into a ``HarnessErrorKind`` that decides whether the router may try the
  next candidate.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hive.runtime.adapter_config import AdapterConfig
    from hive.runtime.base import Runtime
    from hive.runtime.gate_coordinator import GateCoordinator


class RunMode(StrEnum):
    """How a harness is driven for a turn."""

    HEADLESS = "headless"  # one non-interactive subprocess per turn (`claude -p`, `pi -p`)
    PTY = "pty"  # a persistent interactive session driven through a pseudo-terminal


class HarnessErrorKind(StrEnum):
    """Why a run failed — decides whether falling back is safe and useful."""

    AUTH = "auth"  # not logged in / credentials rejected: nothing on this harness will work
    QUOTA = "quota"  # usage/credit exhausted or rate-limited: this mode is out, PTY may not be
    REFUSED = "refused"  # harness declined to run in this mode (e.g. headless not allowed)
    UNAVAILABLE = "unavailable"  # binary missing or could not be started
    OTHER = "other"  # anything else (crash, timeout, model error): do NOT fall back


# Only kinds where the harness failed *before doing any work* may fall back: a
# retry on another harness/mode after a half-run turn could repeat tool side
# effects (double commits, double sends). AUTH/QUOTA/REFUSED/UNAVAILABLE are all
# refusals up front; OTHER may be mid-turn, so it surfaces instead.
_FALLBACK_KINDS = frozenset(
    {
        HarnessErrorKind.AUTH,
        HarnessErrorKind.QUOTA,
        HarnessErrorKind.REFUSED,
        HarnessErrorKind.UNAVAILABLE,
    }
)


class HarnessError(RuntimeError):
    """A run on one (harness, mode) failed; ``kind`` says why."""

    def __init__(
        self,
        kind: HarnessErrorKind,
        harness: str,
        mode: RunMode | None,
        detail: str,
    ) -> None:
        self.kind = kind
        self.harness = harness
        self.mode = mode
        self.detail = detail
        where = f"{harness} ({mode.value})" if mode is not None else harness
        super().__init__(f"{where}: {kind.value} — {detail}")

    @property
    def falls_back(self) -> bool:
        """True when the router may safely try the next candidate."""
        return self.kind in _FALLBACK_KINDS


@dataclass(frozen=True)
class HarnessStatus:
    """Probe result for one harness."""

    name: str
    installed: bool
    # None = could not tell (probe timed out / unparseable): treated as usable so
    # the real run's own error decides, rather than a flaky probe locking a
    # working harness out.
    signed_in: bool | None
    detail: str = ""
    # Whether Hive has an adapter for it (False for detected-but-unsupported).
    has_adapter: bool = True

    @property
    def usable(self) -> bool:
        return self.installed and self.signed_in is not False and self.has_adapter

    def describe(self) -> str:
        if not self.installed:
            state = "not installed"
        elif self.signed_in is False:
            state = "installed, not signed in"
        elif not self.has_adapter:
            state = "installed and signed in, but Hive has no adapter for it yet"
        elif self.signed_in is None:
            state = "installed (sign-in state unknown)"
        else:
            state = "installed and signed in"
        return f"{self.name}: {state}" + (f" — {self.detail}" if self.detail else "")


@dataclass
class RuntimeContext:
    """Per-entity wiring a spec's ``build`` needs to make a ``Runtime``."""

    config: AdapterConfig
    cwd: Path | None = None
    gate_coordinator: GateCoordinator | None = None
    entity_name: str | None = None
    gate_approver: str = "user"
    on_gate_state: Callable[[str, str], None] | None = None
    # A prior session id (persisted on the Entity) to resume on the first turn.
    resume_session_id: str | None = None


@dataclass(frozen=True)
class HarnessSpec:
    """One harness Hive knows about."""

    name: str
    # Blocking probe (runs in a worker thread); must never raise.
    probe: Callable[[], HarnessStatus]
    # Modes this harness's adapter implements, in no particular order — the
    # configured RUN_MODE_ORDER ranks them. Empty = detect-only (no adapter yet).
    modes: tuple[RunMode, ...] = ()
    # Build a Runtime for one mode; None for detect-only specs.
    build: Callable[[RunMode, RuntimeContext], Runtime] | None = None
    # Does the harness understand Claude Code's native ``/goal`` slash command?
    native_goal: bool = False
    # One-line, user-facing fix for "installed but signed out".
    login_hint: str = ""

    @property
    def has_adapter(self) -> bool:
        return self.build is not None and bool(self.modes)


@dataclass(frozen=True)
class Candidate:
    """One (harness, mode) the router may try."""

    harness: str
    mode: RunMode


def plan_candidates(
    specs: Mapping[str, HarnessSpec],
    statuses: Mapping[str, HarnessStatus],
    harness_order: Sequence[str],
    mode_order: Sequence[str],
) -> list[Candidate]:
    """Ordered (harness, mode) attempts: harness-major, headless before PTY.

    A harness appears only if its probe says usable and it has an adapter.
    Harnesses missing from ``harness_order`` are appended afterwards (still in
    registry order) so a newly registered adapter is picked up without a config
    edit, but never ahead of an explicit preference.
    """
    order = [h for h in harness_order if h in specs]
    order += [h for h in specs if h not in order]
    mode_rank = {m: i for i, m in enumerate(mode_order)}
    out: list[Candidate] = []
    for name in order:
        spec = specs[name]
        status = statuses.get(name)
        if status is None or not status.usable:
            continue
        modes = sorted(spec.modes, key=lambda m: mode_rank.get(m.value, len(mode_rank)))
        # A mode absent from RUN_MODE_ORDER is switched off (e.g. "headless" only
        # = never spawn a PTY), not just ranked last.
        out.extend(Candidate(name, m) for m in modes if m.value in mode_rank)
    return out


@dataclass
class DetectionCache:
    value: dict[str, HarnessStatus] = field(default_factory=dict)
    at: float = 0.0


class HarnessDetector:
    """Probes every registered harness and caches the answer briefly.

    Probes shell out (``claude auth status``, ``pi --list-models``) so they run
    in worker threads, concurrently, and are cached for ``ttl`` seconds — a turn
    must not pay a subprocess per harness. ``invalidate()`` forces a re-probe
    (called after an AUTH failure so a fresh login is noticed next turn).
    """

    def __init__(
        self,
        specs: Mapping[str, HarnessSpec],
        ttl: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.specs = specs
        self._ttl = ttl
        self._clock = clock
        self._cache = DetectionCache()
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._cache = DetectionCache()

    async def detect(self, force: bool = False) -> dict[str, HarnessStatus]:
        async with self._lock:
            fresh = self._cache.value and (self._clock() - self._cache.at) < self._ttl
            if fresh and not force:
                return self._cache.value
            names = list(self.specs)
            results = await asyncio.gather(
                *(asyncio.to_thread(self._safe_probe, self.specs[n]) for n in names)
            )
            self._cache = DetectionCache(dict(zip(names, results, strict=True)), self._clock())
            return self._cache.value

    @staticmethod
    def _safe_probe(spec: HarnessSpec) -> HarnessStatus:
        try:
            status = spec.probe()
        except Exception as e:  # a probe must never take down a turn
            return HarnessStatus(spec.name, True, None, f"probe failed: {e}", spec.has_adapter)
        # The spec, not the probe, is the authority on whether an adapter exists.
        if status.has_adapter != spec.has_adapter:
            status = HarnessStatus(
                status.name, status.installed, status.signed_in, status.detail, spec.has_adapter
            )
        return status


class NoUsableHarnessError(RuntimeError):
    """No (harness, mode) can run a turn. ``str()`` is Telegram-ready."""

    def __init__(
        self,
        statuses: Mapping[str, HarnessStatus],
        specs: Mapping[str, HarnessSpec],
        attempts: Sequence[HarnessError] = (),
    ) -> None:
        self.statuses = dict(statuses)
        self.attempts = list(attempts)
        lines = ["⚠️ No usable agent harness — Hive can't run this turn."]
        failed = {a.harness: a for a in attempts}
        for name, spec in specs.items():
            status = statuses.get(name)
            line = f"• {status.describe() if status else name + ': not probed'}"
            if name in failed:
                line += f"\n  last error: {failed[name].kind.value} — {failed[name].detail}"
            elif status is not None and status.usable is False and status.signed_in is False:
                if spec.login_hint:
                    line += f"\n  fix: {spec.login_hint}"
            lines.append(line)
        for a in attempts:
            hint = specs[a.harness].login_hint if a.harness in specs else ""
            if a.kind is HarnessErrorKind.AUTH and hint:
                lines.append(f"{a.harness} is signed out — {hint}")
        lines.append("Sign in to any one of them and resend; Hive picks it up automatically.")
        super().__init__("\n".join(lines))
