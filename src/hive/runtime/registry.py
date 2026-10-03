"""The harnesses Hive knows about — the single place to register a new one.

To add a harness (OpenCode T016, a direct model-API harness): write
its adapter(s) implementing ``Runtime``, write a ``probe`` that answers
installed/signed-in without spending credit, and add one ``HarnessSpec`` below.
Detection, preference ordering, fallback and the "no usable harness" message are
all driven off the specs — nothing else changes.
"""

from __future__ import annotations

import hive.config as config
from hive.runtime.claude_adapter import ClaudeAdapter
from hive.runtime.claude_headless import ClaudeHeadlessAdapter, probe_claude
from hive.runtime.codex_adapter import CodexAdapter, probe_codex
from hive.runtime.harness import (
    HarnessDetector,
    HarnessSpec,
    NoUsableHarnessError,
    RunMode,
    RuntimeContext,
    plan_candidates,
)
from hive.runtime.pi_adapter import PiAdapter, probe_pi


def _build_claude(mode: RunMode, ctx: RuntimeContext):
    if mode is RunMode.HEADLESS:
        return ClaudeHeadlessAdapter(ctx.config, ctx.cwd, ctx.resume_session_id)
    return ClaudeAdapter(
        ctx.config,
        cwd=ctx.cwd,
        gate_coordinator=ctx.gate_coordinator,
        entity_name=ctx.entity_name,
        gate_approver=ctx.gate_approver,
        on_gate_state=ctx.on_gate_state,
    )


def _build_pi(mode: RunMode, ctx: RuntimeContext):
    return PiAdapter(ctx.config, ctx.cwd, ctx.resume_session_id)


def _build_codex(mode: RunMode, ctx: RuntimeContext):
    return CodexAdapter(ctx.config, ctx.cwd, ctx.resume_session_id)


def default_specs() -> dict[str, HarnessSpec]:
    return {
        "pi": HarnessSpec(
            name="pi",
            probe=probe_pi,
            modes=(RunMode.HEADLESS,),
            build=_build_pi,
            login_hint="run `pi`, then /login (or export a provider API key)",
        ),
        "claude": HarnessSpec(
            name="claude",
            probe=probe_claude,
            modes=(RunMode.HEADLESS, RunMode.PTY),
            build=_build_claude,
            native_goal=True,
            enforces_fence=True,
            login_hint="run `claude auth login` on the host",
        ),
        "codex": HarnessSpec(
            name="codex",
            probe=probe_codex,
            modes=(RunMode.HEADLESS,),
            build=_build_codex,
            login_hint="run `codex login` on the host",
        ),
    }


def default_detector() -> HarnessDetector:
    return HarnessDetector(default_specs(), ttl=config.HARNESS_DETECT_TTL_S)


async def availability_report(
    detector: HarnessDetector,
) -> tuple[list[str], NoUsableHarnessError | None]:
    """Startup summary: one line per harness and one run order per distinct role
    order, plus the error to alert on when no (harness, mode) can run a turn (e.g.
    Claude Code logged out and Pi unconfigured)."""
    statuses = await detector.detect(force=True)
    lines = [s.describe() for s in statuses.values()]
    groups: dict[tuple[str, ...], list[str]] = {tuple(config.HARNESS_ORDER): ["default"]}
    for role in config.ROLES:
        groups.setdefault(tuple(config.harness_order_for(role)), []).append(role)
    for order, roles in groups.items():
        plan = plan_candidates(detector.specs, statuses, order, config.RUN_MODE_ORDER)
        if not plan:
            return lines, NoUsableHarnessError(statuses, detector.specs)
        steps = " → ".join(f"{c.harness} ({c.mode.value})" for c in plan)
        lines.append(f"run order ({', '.join(roles)}): {steps}")
    return lines, None
