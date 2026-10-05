"""The default harness registry and the startup availability report."""

from __future__ import annotations

import hive.config as config
from hive.runtime.adapter_config import AdapterConfig
from hive.runtime.claude_adapter import ClaudeAdapter
from hive.runtime.claude_headless import ClaudeHeadlessAdapter
from hive.runtime.codex_adapter import CodexAdapter
from hive.runtime.harness import HarnessDetector, HarnessStatus, RunMode, RuntimeContext
from hive.runtime.pi_adapter import PiAdapter
from hive.runtime.registry import availability_report, default_specs

CTX = RuntimeContext(AdapterConfig(name="otter", role="vault"))


def test_default_specs_cover_codex_claude_and_pi() -> None:
    specs = default_specs()
    assert set(specs) == {"pi", "claude", "codex"}
    assert specs["pi"].modes == (RunMode.HEADLESS,)  # no Pi PTY driver
    assert set(specs["claude"].modes) == {RunMode.HEADLESS, RunMode.PTY}
    assert specs["codex"].modes == (RunMode.HEADLESS,)
    assert specs["codex"].has_adapter
    assert specs["claude"].native_goal and not specs["pi"].native_goal


def test_specs_build_the_right_runtime_per_mode() -> None:
    specs = default_specs()
    assert isinstance(specs["pi"].build(RunMode.HEADLESS, CTX), PiAdapter)
    assert isinstance(specs["claude"].build(RunMode.HEADLESS, CTX), ClaudeHeadlessAdapter)
    assert isinstance(specs["claude"].build(RunMode.PTY, CTX), ClaudeAdapter)
    assert isinstance(specs["codex"].build(RunMode.HEADLESS, CTX), CodexAdapter)


def test_default_preference_is_codex_then_claude_then_pi() -> None:
    assert config.HARNESS_ORDER == ["codex", "claude", "pi"]
    assert config.RUN_MODE_ORDER == ["headless", "pty"]


def test_role_model_defaults_and_overrides(monkeypatch) -> None:
    specs = default_specs()
    for role, expected in (("vault", "claude-sonnet-5-5"),):
        monkeypatch.delenv(f"HIVE_CLAUDE_MODEL_{role.upper()}", raising=False)
        ctx = RuntimeContext(AdapterConfig(name="n", role=role))
        headless = specs["claude"].build(RunMode.HEADLESS, ctx)
        pty = specs["claude"].build(RunMode.PTY, ctx)
        assert headless._config.model == pty._config.model == expected
        assert ctx.config.model == ""  # the shared context is not mutated

    monkeypatch.setenv("HIVE_CLAUDE_MODEL_VAULT", "custom-sonnet")
    assert config.claude_model_for("vault") == "custom-sonnet"
    monkeypatch.setenv("HIVE_CODEX_MODEL_VAULT", "custom-sol")
    monkeypatch.setenv("HIVE_CODEX_EFFORT_VAULT", "high")
    monkeypatch.setenv("HIVE_PI_MODEL_VAULT", "custom-pi")
    assert config.codex_model_for("vault") == "custom-sol"
    assert config.codex_effort_for("vault") == "high"
    assert config.pi_model_for("vault") == "custom-pi"


def _detector(pi: bool | None, claude: bool | None) -> HarnessDetector:
    specs = default_specs()
    probes = {
        "pi": HarnessStatus("pi", True, pi),
        "claude": HarnessStatus("claude", True, claude, "Claude Code is logged out"),
        "codex": HarnessStatus("codex", True, False),
    }
    return HarnessDetector(
        {n: type(s)(**{**s.__dict__, "probe": (lambda n=n: probes[n])}) for n, s in specs.items()}
    )


def _default_orders(monkeypatch) -> None:
    monkeypatch.setattr(config, "HARNESS_ORDER", ["pi", "claude"])
    monkeypatch.setattr(config, "RUN_MODE_ORDER", ["headless", "pty"])
    for role in config.ROLES:
        monkeypatch.delenv(f"HIVE_HARNESS_ORDER_{role.upper()}", raising=False)


async def test_report_lists_run_order_when_something_is_usable(monkeypatch) -> None:
    _default_orders(monkeypatch)
    lines, problem = await availability_report(_detector(pi=True, claude=False))
    assert problem is None
    assert lines[-2:] == [
        "run order (default): pi (headless)",
        "run order (vault): pi (headless)",
    ]
    assert any("claude: installed, not signed in" in ln for ln in lines)


async def test_report_shows_each_distinct_role_order(monkeypatch) -> None:
    _default_orders(monkeypatch)
    monkeypatch.setenv("HIVE_HARNESS_ORDER_VAULT", "pi,claude")
    lines, problem = await availability_report(_detector(pi=True, claude=True))
    assert problem is None
    assert lines[-1:] == [
        "run order (default, vault): pi (headless) → claude (headless) → claude (pty)",
    ]


async def test_report_shows_a_vault_override(monkeypatch) -> None:
    _default_orders(monkeypatch)
    monkeypatch.setenv("HIVE_HARNESS_ORDER_VAULT", "claude,pi")
    lines, problem = await availability_report(_detector(pi=True, claude=True))
    assert problem is None
    assert lines[-2:] == [
        "run order (default): pi (headless) → claude (headless) → claude (pty)",
        "run order (vault): claude (headless) → claude (pty) → pi (headless)",
    ]


async def test_report_flags_no_usable_harness_when_claude_is_logged_out() -> None:
    lines, problem = await availability_report(_detector(pi=False, claude=False))
    assert problem is not None
    msg = str(problem)
    assert "No usable agent harness" in msg and "Claude Code is logged out" in msg
    assert "claude auth login" in msg


def test_explicit_model_preserved_in_both_claude_modes():
    for role in ("vault",):
        ctx = RuntimeContext(AdapterConfig(role=role, model="haiku"))
        for mode in (RunMode.HEADLESS, RunMode.PTY):
            runtime = default_specs()["claude"].build(mode, ctx)
            assert runtime._config.model == "haiku"
