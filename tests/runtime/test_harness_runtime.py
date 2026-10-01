"""HarnessRuntime: per-turn harness/mode selection and fallback, with fake runtimes."""

from __future__ import annotations

import pytest

import hive.config as config
from hive.runtime.adapter_config import AdapterConfig
from hive.runtime.base import Runtime
from hive.runtime.harness import (
    HarnessDetector,
    HarnessError,
    HarnessErrorKind,
    HarnessSpec,
    HarnessStatus,
    NoUsableHarnessError,
    RunMode,
    RuntimeContext,
)
from hive.runtime.harness_runtime import HarnessRuntime

H, P = RunMode.HEADLESS, RunMode.PTY
K = HarnessErrorKind


class FakeRuntime(Runtime):
    """Scripted: ``script`` entries are returned as text or raised if exceptions."""

    def __init__(self, harness: str, mode: RunMode, script: list, log: list) -> None:
        self.harness, self.mode, self.script, self.log = harness, mode, script, log
        self.alive = False

    async def start(self) -> None:
        self.alive = True
        self.log.append(("start", self.harness, self.mode))

    async def stop(self) -> None:
        self.alive = False
        self.log.append(("stop", self.harness, self.mode))

    def is_alive(self) -> bool:
        return self.alive

    async def send_turn(self, prompt: str):
        self.log.append(("turn", self.harness, self.mode, prompt))
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return step, {"input_tokens": 1, "output_tokens": 1, "session_id": f"{self.harness}-s"}


class FakeHeadlessRuntime(FakeRuntime):
    """Headless modes can follow a session; the PTY (plain FakeRuntime) cannot."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.adopted: list[str] = []

    def adopt_session(self, sid) -> None:
        self.adopted.append(sid)


class Env:
    """Wires fake specs + a detector + a HarnessRuntime."""

    def __init__(
        self,
        scripts: dict,
        statuses: dict,
        native_goal=("claude",),
        role: str = "lead",
        harness_order: tuple[str, ...] | None = ("pi", "claude"),
    ) -> None:
        self.log: list = []
        self.runtimes: dict[tuple, FakeRuntime] = {}
        modes = {"pi": (H,), "claude": (H, P)}
        specs = {}
        for name in ("pi", "claude"):
            specs[name] = HarnessSpec(
                name=name,
                probe=lambda n=name: self.statuses[n],
                modes=modes[name],
                build=self._builder(name, scripts),
                native_goal=name in native_goal,
                enforces_fence=name == "claude",
                login_hint=f"login to {name}",
            )
        self.statuses = statuses
        self.detector = HarnessDetector(specs, ttl=0)  # re-probe each turn
        self.now = 0.0
        self.rt = HarnessRuntime(
            RuntimeContext(AdapterConfig(name="otter", role=role)),
            self.detector,
            harness_order=harness_order,
            mode_order=["headless", "pty"],
            clock=lambda: self.now,
        )

    def _builder(self, name, scripts):
        def build(mode, ctx):
            cls = FakeRuntime if mode is P else FakeHeadlessRuntime
            rt = cls(name, mode, scripts[(name, mode)], self.log)
            self.runtimes[(name, mode)] = rt
            return rt

        return build

    def ran(self) -> list[tuple]:
        return [(e[1], e[2].value) for e in self.log if e[0] == "turn"]


def _status(pi=True, claude=True) -> dict:
    return {
        "pi": HarnessStatus("pi", True, pi),
        "claude": HarnessStatus("claude", True, claude),
    }


def _auth(h, m=H):
    return HarnessError(K.AUTH, h, m, "Not logged in")


def _quota(h, m=H):
    return HarnessError(K.QUOTA, h, m, "You've hit your limit")


@pytest.fixture(autouse=True)
def _cooldowns(monkeypatch) -> None:
    monkeypatch.setattr(config, "HEADLESS_QUOTA_RETRY_S", 900.0)
    monkeypatch.setattr(config, "HARNESS_RETRY_S", 60.0)


async def test_pi_headless_is_the_default() -> None:
    env = Env({("pi", H): ["from pi"], ("claude", H): ["c"], ("claude", P): ["p"]}, _status())
    await env.rt.start()

    text, usage = await env.rt.send_turn("hi")

    assert text == "from pi"
    assert (usage["harness"], usage["mode"], usage["fell_back"]) == ("pi", "headless", [])
    assert env.rt.last_run.label() == "pi (headless)"
    assert env.ran() == [("pi", "headless")]
    # Nothing but the chosen runtime is even built — a PTY costs RAM.
    assert list(env.runtimes) == [("pi", H)]


async def test_start_spawns_nothing() -> None:
    env = Env({}, _status())
    await env.rt.start()
    assert env.log == [] and env.rt.is_alive()


async def test_claude_logged_out_and_pi_ready_uses_pi() -> None:
    env = Env({("pi", H): ["pi!"]}, _status(claude=False))
    await env.rt.start()
    assert (await env.rt.send_turn("x"))[1]["harness"] == "pi"


async def test_pi_unusable_falls_to_claude_headless() -> None:
    env = Env({("claude", H): ["c!"]}, _status(pi=False))
    await env.rt.start()
    text, usage = await env.rt.send_turn("x")
    assert (text, usage["harness"], usage["mode"]) == ("c!", "claude", "headless")


async def test_headless_quota_falls_back_to_pty_with_the_reason() -> None:
    scripts = {("claude", H): [_quota("claude")], ("claude", P): ["pty!"]}
    env = Env(scripts, _status(pi=False))
    await env.rt.start()

    text, usage = await env.rt.send_turn("x")

    assert text == "pty!"
    assert (usage["harness"], usage["mode"]) == ("claude", "pty")
    assert len(usage["fell_back"]) == 1 and "hit your limit" in usage["fell_back"][0]
    assert env.ran() == [("claude", "headless"), ("claude", "pty")]


async def test_pi_failure_falls_through_to_claude_headless_then_pty() -> None:
    scripts = {
        ("pi", H): [_auth("pi")],
        ("claude", H): [_quota("claude")],
        ("claude", P): ["pty!"],
    }
    env = Env(scripts, _status())
    await env.rt.start()
    _, usage = await env.rt.send_turn("x")
    assert (usage["harness"], usage["mode"]) == ("claude", "pty")
    assert len(usage["fell_back"]) == 2


async def test_quota_cooldown_skips_headless_then_retries_it() -> None:
    scripts = {("claude", H): [_quota("claude"), "back on headless"], ("claude", P): ["pty!"]}
    env = Env(scripts, _status(pi=False))
    await env.rt.start()
    await env.rt.send_turn("1")  # headless quota -> pty
    env.log.clear()

    await env.rt.send_turn("2")  # inside the cooldown: straight to pty, no failed headless try
    assert env.ran() == [("claude", "pty")]

    env.now = 901
    env.log.clear()
    _, usage = await env.rt.send_turn("3")  # cooldown over: headless is tried again
    assert (usage["harness"], usage["mode"]) == ("claude", "headless")


async def test_auth_failure_blocks_the_whole_harness_including_pty() -> None:
    scripts = {("claude", H): [_auth("claude")], ("claude", P): ["must not run"]}
    env = Env(scripts, _status(pi=False))
    await env.rt.start()

    with pytest.raises(NoUsableHarnessError) as ei:
        await env.rt.send_turn("x")

    assert env.ran() == [("claude", "headless")]  # a signed-out harness fails in PTY too
    assert "Not logged in" in str(ei.value) and "login to claude" in str(ei.value)


async def test_no_usable_harness_when_everything_is_signed_out() -> None:
    env = Env({}, _status(pi=False, claude=False))
    await env.rt.start()
    with pytest.raises(NoUsableHarnessError) as ei:
        await env.rt.send_turn("x")
    msg = str(ei.value)
    assert "pi: installed, not signed in" in msg and "claude: installed, not signed in" in msg
    assert env.log == []


async def test_relogin_is_picked_up_next_turn() -> None:
    env = Env({("pi", H): ["pi!"]}, _status(pi=False, claude=False))
    await env.rt.start()
    with pytest.raises(NoUsableHarnessError):
        await env.rt.send_turn("x")

    env.statuses["pi"] = HarnessStatus("pi", True, True)  # the user logs in
    assert (await env.rt.send_turn("x"))[0] == "pi!"


async def test_a_midturn_failure_does_not_replay_on_another_harness() -> None:
    boom = HarnessError(K.OTHER, "pi", H, "provider closed the connection")
    env = Env({("pi", H): [boom], ("claude", H): ["must not run"]}, _status())
    await env.rt.start()

    with pytest.raises(HarnessError) as ei:
        await env.rt.send_turn("x")

    assert ei.value is boom
    assert env.ran() == [("pi", "headless")]


async def test_timeout_from_a_pty_runtime_propagates_for_the_bounce_logic() -> None:
    env = Env(
        {("claude", P): [TimeoutError()], ("claude", H): [_quota("claude")]}, _status(pi=False)
    )
    await env.rt.start()
    with pytest.raises(TimeoutError):
        await env.rt.send_turn("x")


async def test_goal_seed_is_stripped_for_harnesses_without_native_goal() -> None:
    env = Env({("pi", H): ["ok"], ("claude", H): ["ok"]}, _status())
    await env.rt.start()
    await env.rt.send_turn("/goal ship the thing")
    assert env.log[-1][3] == "ship the thing"  # pi: bare goal text

    env2 = Env({("claude", H): ["ok"]}, _status(pi=False))
    await env2.rt.start()
    await env2.rt.send_turn("/goal ship the thing")
    assert env2.log[-1][3] == "/goal ship the thing"  # claude keeps native /goal


async def test_pty_turn_syncs_its_session_to_sibling_headless() -> None:
    scripts = {("claude", H): [_quota("claude")], ("claude", P): ["pty!"]}
    env = Env(scripts, _status(pi=False))
    await env.rt.start()
    await env.rt.send_turn("x")
    assert env.runtimes[("claude", H)].adopted == ["claude-s"]


async def test_stop_stops_every_built_runtime() -> None:
    scripts = {("claude", H): [_quota("claude")], ("claude", P): ["pty!"]}
    env = Env(scripts, _status(pi=False))
    await env.rt.start()
    await env.rt.send_turn("x")
    await env.rt.stop()
    assert {e[1:] for e in env.log if e[0] == "stop"} == {("claude", H), ("claude", P)}
    assert not env.rt.is_alive()


async def test_pty_only_probes_default_safely_without_a_pty() -> None:
    env = Env({("pi", H): ["ok"]}, _status())
    await env.rt.start()
    await env.rt.send_turn("x")
    assert env.rt.poll_workflow_progress() == []
    assert env.rt.workflow_active(60) is False
    assert env.rt.describe_jam() is None
    assert env.rt.is_busy() is False


async def test_headless_success_retires_the_stale_pty_so_it_respawns_on_the_live_session() -> None:
    # quota -> PTY; cooldown passes -> headless serves; quota again -> PTY must be a
    # fresh spawn (`--continue`), not the old process that never saw the headless turns.
    scripts = {
        ("claude", H): [_quota("claude"), "headless", _quota("claude")],
        ("claude", P): ["pty"],
    }
    env = Env(scripts, _status(pi=False))
    await env.rt.start()
    await env.rt.send_turn("1")
    first_pty = env.runtimes[("claude", P)]
    env.now = 1000.0
    await env.rt.send_turn("2")
    assert ("stop", "claude", P) in env.log and not first_pty.alive
    await env.rt.send_turn("3")
    assert env.runtimes[("claude", P)] is not first_pty
    assert [e[1:] for e in env.log if e[0] == "start" and e[2] is P] == [("claude", P)] * 2


async def test_fenced_role_defaults_to_claude_first(monkeypatch) -> None:
    monkeypatch.setattr(config, "HARNESS_ORDER", ["pi", "claude"])
    monkeypatch.delenv("HIVE_HARNESS_ORDER_MAESTRO", raising=False)
    monkeypatch.delenv("HIVE_HARNESS_ORDER_LEAD", raising=False)
    scripts = {("pi", H): ["pi"], ("claude", H): ["claude"]}

    maestro = Env(scripts, _status(), role="maestro", harness_order=None)
    await maestro.rt.start()
    _, usage = await maestro.rt.send_turn("x")
    assert (usage["harness"], usage["unfenced"]) == ("claude", False)

    lead = Env(scripts, _status(), role="lead", harness_order=None)
    await lead.rt.start()
    _, usage = await lead.rt.send_turn("x")
    assert (usage["harness"], usage["unfenced"]) == ("pi", False)


async def test_per_role_env_overrides_the_harness_order(monkeypatch) -> None:
    monkeypatch.setenv("HIVE_HARNESS_ORDER_LEAD", "claude,pi")
    monkeypatch.setenv("HIVE_HARNESS_ORDER_MAESTRO", "pi,claude")
    scripts = {("pi", H): ["pi"], ("claude", H): ["claude"]}

    lead = Env(scripts, _status(), role="lead", harness_order=None)
    await lead.rt.start()
    assert (await lead.rt.send_turn("x"))[1]["harness"] == "claude"

    maestro = Env(scripts, _status(), role="maestro", harness_order=None)
    await maestro.rt.start()
    _, usage = await maestro.rt.send_turn("x")
    assert (usage["harness"], usage["unfenced"]) == ("pi", True)


async def test_fenced_role_on_pi_is_flagged_unfenced(monkeypatch) -> None:
    monkeypatch.delenv("HIVE_HARNESS_ORDER_MAESTRO", raising=False)
    env = Env(
        {("pi", H): ["pi"], ("claude", H): [_auth("claude")]},
        _status(),
        role="maestro",
        harness_order=None,
    )
    await env.rt.start()
    _, usage = await env.rt.send_turn("x")
    assert env.ran() == [("claude", "headless"), ("pi", "headless")]
    assert usage["unfenced"] is True and env.rt.last_run.unfenced
