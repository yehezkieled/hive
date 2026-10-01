"""HarnessRuntime — one Runtime per entity that picks the harness and mode per turn.

ADR 0029. Every turn: probe (cached) which harnesses are installed and signed in,
rank (harness, mode) candidates — Pi first, headless before PTY — and run the turn
on the first that works. A candidate that fails with a *refusal* (auth, quota,
headless refused, binary missing) is skipped for a cooldown and the next one runs
the same turn; any other failure surfaces untouched, because it may have failed
mid-turn and replaying it elsewhere could repeat tool side effects.

The entity (and the rest of Hive) sees one ``Runtime``; which harness/mode served
the turn is reported in the usage dict (``harness``, ``mode``, ``fell_back``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import hive.config as config
from hive.process.loops import unseed_goal
from hive.runtime.base import Runtime
from hive.runtime.harness import (
    Candidate,
    HarnessDetector,
    HarnessError,
    HarnessErrorKind,
    NoUsableHarnessError,
    RunMode,
    RuntimeContext,
    plan_candidates,
)
from hive.runtime.workflow_progress import WorkflowProgress

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunInfo:
    """Which harness and mode served a turn, and why not the preferred one."""

    harness: str
    mode: RunMode
    # Failures of earlier candidates this turn, e.g. ["pi (headless): auth — …"].
    fell_back_from: tuple[str, ...] = ()
    # A fenced role (config.FENCED_ROLES) ran on a harness that does not enforce
    # the ownership guard — the user must be told the fence is off.
    unfenced: bool = False

    def label(self) -> str:
        return f"{self.harness} ({self.mode.value})"


class HarnessRuntime(Runtime):
    def __init__(
        self,
        ctx: RuntimeContext,
        detector: HarnessDetector,
        *,
        harness_order: Sequence[str] | None = None,
        mode_order: Sequence[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ctx = ctx
        self._detector = detector
        self._harness_order = list(harness_order or config.harness_order_for(ctx.config.role))
        self._fenced = ctx.config.role in config.FENCED_ROLES
        self._mode_order = list(mode_order or config.RUN_MODE_ORDER)
        self._clock = clock
        self._runtimes: dict[Candidate, Runtime] = {}
        # (harness, mode|None) -> monotonic deadline. mode None = the whole harness.
        self._blocked: dict[tuple[str, RunMode | None], float] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._last: Runtime | None = None
        self.last_run: RunInfo | None = None

    # -- Runtime ---------------------------------------------------------
    async def start(self) -> None:
        # No subprocess or PTY is spawned here: runtimes are built lazily, only
        # for the candidate a turn actually uses (a PTY costs RAM; headless is free).
        self._started = True

    async def stop(self) -> None:
        self._started = False
        runtimes, self._runtimes = list(self._runtimes.values()), {}
        for rt in runtimes:
            try:
                await rt.stop()
            except Exception:
                logger.exception("failed to stop a %s runtime", type(rt).__name__)
        self._last = None

    def is_alive(self) -> bool:
        return self._started and (self._last is None or self._last.is_alive())

    def is_busy(self) -> bool:
        return self._lock.locked()

    # -- PTY-only probes (the manager duck-types these off the adapter) ----
    def _pty(self) -> Runtime | None:
        return next((rt for c, rt in self._runtimes.items() if c.mode is RunMode.PTY), None)

    def poll_workflow_progress(self) -> list[WorkflowProgress]:
        pty = self._pty()
        return pty.poll_workflow_progress() if pty is not None else []  # type: ignore[attr-defined]

    def workflow_active(self, window: float) -> bool:
        pty = self._pty()
        return pty.workflow_active(window) if pty is not None else False  # type: ignore[attr-defined]

    def describe_jam(self) -> dict | None:
        pty = self._pty()
        return pty.describe_jam() if pty is not None else None  # type: ignore[attr-defined]

    # -- selection -------------------------------------------------------
    def _is_blocked(self, c: Candidate) -> bool:
        now = self._clock()
        for key in ((c.harness, None), (c.harness, c.mode)):
            until = self._blocked.get(key)
            if until is not None:
                if until > now:
                    return True
                del self._blocked[key]
        return False

    def _block(self, c: Candidate, err: HarnessError) -> None:
        if err.kind in (HarnessErrorKind.QUOTA, HarnessErrorKind.REFUSED):
            # This mode is out; the harness's other modes may still work.
            ttl = config.HEADLESS_QUOTA_RETRY_S
            key = (c.harness, c.mode)
        else:  # AUTH / UNAVAILABLE: the whole harness
            ttl = config.HARNESS_RETRY_S
            key = (c.harness, None)
        self._blocked[key] = self._clock() + ttl

    async def _runtime_for(self, c: Candidate) -> Runtime:
        rt = self._runtimes.get(c)
        if rt is not None and rt.is_alive():
            return rt
        spec = self._detector.specs[c.harness]
        assert spec.build is not None  # plan_candidates only yields buildable specs
        rt = spec.build(c.mode, self._ctx)
        try:
            await rt.start()
        except Exception as e:
            # Failed to even start (e.g. the PTY could not spawn): no work was done.
            raise HarnessError(
                HarnessErrorKind.UNAVAILABLE, c.harness, c.mode, f"failed to start: {e}"
            ) from e
        self._runtimes[c] = rt
        return rt

    # -- the turn --------------------------------------------------------
    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        async with self._lock:
            assert self._started, "HarnessRuntime not started — call start() first"
            statuses = await self._detector.detect()
            candidates = plan_candidates(
                self._detector.specs, statuses, self._harness_order, self._mode_order
            )
            attempts: list[HarnessError] = []
            for cand in candidates:
                if self._is_blocked(cand):
                    continue
                try:
                    rt = await self._runtime_for(cand)
                    text, usage = await rt.send_turn(self._adapt_prompt(prompt, cand))
                except HarnessError as err:
                    logger.warning("harness %s/%s failed: %s", cand.harness, cand.mode, err)
                    if not err.falls_back:
                        raise
                    attempts.append(err)
                    self._block(cand, err)
                    if err.kind is HarnessErrorKind.AUTH:
                        self._detector.invalidate()  # re-probe soon: the user may re-login
                    continue
                self._last = rt
                await self._sync_sessions(cand, usage.get("session_id"))
                unfenced = self._fenced and not self._detector.specs[cand.harness].enforces_fence
                info = RunInfo(cand.harness, cand.mode, tuple(str(a) for a in attempts), unfenced)
                self.last_run = info
                usage = {
                    **usage,
                    "harness": cand.harness,
                    "mode": cand.mode.value,
                    "fell_back": [str(a) for a in attempts],
                    "unfenced": unfenced,
                }
                return text, usage
            # Nothing ran. Re-probe next time: this may be a stale "signed in".
            self._detector.invalidate()
            raise NoUsableHarnessError(statuses, self._detector.specs, attempts)

    def _adapt_prompt(self, prompt: str, cand: Candidate) -> str:
        """Hive seeds Claude Code's native ``/goal`` harness-blind; a harness that
        does not understand the slash command gets the bare goal text instead."""
        if self._detector.specs[cand.harness].native_goal:
            return prompt
        return unseed_goal(prompt)

    async def _sync_sessions(self, ran: Candidate, session_id: str | None) -> None:
        """Keep sibling modes of one harness on the same conversation, so a PTY
        fallback turn is not forgotten when headless resumes later.

        A sibling that cannot adopt a session (the PTY holds its conversation
        in-process) is stopped instead, so a later fallback respawns it with
        ``--continue`` on the current conversation rather than a stale one.
        """
        for cand, rt in list(self._runtimes.items()):
            if cand.harness != ran.harness or cand == ran:
                continue
            if hasattr(rt, "adopt_session"):
                rt.adopt_session(session_id)
                continue
            del self._runtimes[cand]
            try:
                await rt.stop()
            except Exception:
                logger.exception("failed to stop a %s runtime", type(rt).__name__)
