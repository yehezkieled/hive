"""The default harness registry and the startup availability report."""

from __future__ import annotations

import hive.config as config
from hive.runtime.adapter_config import AdapterConfig
from hive.runtime.claude_adapter import ClaudeAdapter
from hive.runtime.claude_headless import ClaudeHeadlessAdapter
from hive.runtime.harness import HarnessDetector, HarnessStatus, RunMode, RuntimeContext
from hive.runtime.pi_adapter import PiAdapter
from hive.runtime.registry import availability_report, default_specs

CTX = RuntimeContext(AdapterConfig(name="otter", role="lead"))


def test_default_specs_cover_pi_claude_and_detect_only_codex() -> None:
    specs = default_specs()
    assert set(specs) == {"pi", "claude", "codex"}
    assert specs["pi"].modes == (RunMode.HEADLESS,)  # no Pi PTY driver
    assert set(specs["claude"].modes) == {RunMode.HEADLESS, RunMode.PTY}
    assert not specs["codex"].has_adapter  # T015 extension point: detect only
    assert specs["claude"].native_goal and not specs["pi"].native_goal


def test_specs_build_the_right_runtime_per_mode() -> None:
    specs = default_specs()
    assert isinstance(specs["pi"].build(RunMode.HEADLESS, CTX), PiAdapter)
    assert isinstance(specs["claude"].build(RunMode.HEADLESS, CTX), ClaudeHeadlessAdapter)
    assert isinstance(specs["claude"].build(RunMode.PTY, CTX), ClaudeAdapter)


def test_default_preference_is_pi_then_claude_headless_first() -> None:
    assert config.HARNESS_ORDER[:2] == ["pi", "claude"]
    assert config.RUN_MODE_ORDER == ["headless", "pty"]


def _detector(pi: bool | None, claude: bool | None) -> HarnessDetector:
    specs = default_specs()
    probes = {
        "pi": HarnessStatus("pi", True, pi),
        "claude": HarnessStatus("claude", True, claude, "Claude Code is logged out"),
        "codex": HarnessStatus("codex", True, True, has_adapter=False),
    }
    return HarnessDetector(
        {n: type(s)(**{**s.__dict__, "probe": (lambda n=n: probes[n])}) for n, s in specs.items()}
    )


def _default_orders(monkeypatch) -> None:
    monkeypatch.setattr(config, "HARNESS_ORDER", ["pi", "claude"])
    monkeypatch.setattr(config, "RUN_MODE_ORDER", ["headless", "pty"])
    for role in config.FENCED_ROLES:
        monkeypatch.delenv(f"HIVE_HARNESS_ORDER_{role.upper()}", raising=False)


async def test_report_lists_run_order_when_something_is_usable(monkeypatch) -> None:
    _default_orders(monkeypatch)
    lines, problem = await availability_report(_detector(pi=True, claude=False))
    assert problem is None
    assert lines[-2:] == [
        "run order (default): pi (headless)",
        "run order (maestro, vault): pi (headless)",
    ]
    assert any("claude: installed, not signed in" in ln for ln in lines)


async def test_report_shows_each_distinct_role_order(monkeypatch) -> None:
    _default_orders(monkeypatch)
    monkeypatch.setenv("HIVE_HARNESS_ORDER_VAULT", "pi,claude")
    lines, problem = await availability_report(_detector(pi=True, claude=True))
    assert problem is None
    assert lines[-2:] == [
        "run order (default, vault): pi (headless) → claude (headless) → claude (pty)",
        "run order (maestro): claude (headless) → claude (pty) → pi (headless)",
    ]


async def test_report_flags_no_usable_harness_when_claude_is_logged_out() -> None:
    lines, problem = await availability_report(_detector(pi=False, claude=False))
    assert problem is not None
    msg = str(problem)
    assert "No usable agent harness" in msg and "Claude Code is logged out" in msg
    assert "claude auth login" in msg
