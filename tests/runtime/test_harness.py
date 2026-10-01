"""Harness registry: candidate ranking, probe caching, the no-usable message."""

from __future__ import annotations

from hive.runtime.harness import (
    Candidate,
    HarnessDetector,
    HarnessError,
    HarnessErrorKind,
    HarnessSpec,
    HarnessStatus,
    NoUsableHarnessError,
    RunMode,
    plan_candidates,
)

H, P = RunMode.HEADLESS, RunMode.PTY


def _spec(name, modes=(H,), build=True, hint="") -> HarnessSpec:
    return HarnessSpec(
        name=name,
        probe=lambda: HarnessStatus(name, True, True),
        modes=modes if build else (),
        build=(lambda m, c: None) if build else None,
        login_hint=hint,
    )


def _ok(name, **kw) -> HarnessStatus:
    return HarnessStatus(name, True, True, **kw)


SPECS = {
    "pi": _spec("pi"),
    "claude": _spec("claude", (P, H)),  # declared PTY-first on purpose
    "codex": _spec("codex", build=False),
}
ALL_OK = {n: _ok(n) for n in SPECS}


def test_pi_first_headless_before_pty() -> None:
    got = plan_candidates(SPECS, ALL_OK, ["pi", "claude"], ["headless", "pty"])
    assert got == [Candidate("pi", H), Candidate("claude", H), Candidate("claude", P)]


def test_harness_order_is_configurable() -> None:
    got = plan_candidates(SPECS, ALL_OK, ["claude", "pi"], ["headless", "pty"])
    assert [c.harness for c in got] == ["claude", "claude", "pi"]


def test_pty_first_when_configured() -> None:
    got = plan_candidates(SPECS, ALL_OK, ["claude"], ["pty", "headless"])
    assert got[:2] == [Candidate("claude", P), Candidate("claude", H)]


def test_mode_missing_from_order_is_disabled() -> None:
    got = plan_candidates(SPECS, ALL_OK, ["claude"], ["headless"])
    assert Candidate("claude", P) not in got


def test_signed_out_and_missing_harnesses_are_skipped() -> None:
    statuses = {
        "pi": HarnessStatus("pi", True, False, "no provider"),
        "claude": HarnessStatus("claude", False, None),
        "codex": _ok("codex"),
    }
    assert plan_candidates(SPECS, statuses, ["pi", "claude"], ["headless", "pty"]) == []


def test_unknown_signin_state_is_attempted() -> None:
    statuses = {**ALL_OK, "pi": HarnessStatus("pi", True, None, "probe timed out")}
    got = plan_candidates(SPECS, statuses, ["pi"], ["headless"])
    assert got[0] == Candidate("pi", H)


def test_detect_only_harness_never_becomes_a_candidate() -> None:
    got = plan_candidates(SPECS, ALL_OK, ["codex", "pi"], ["headless", "pty"])
    assert all(c.harness != "codex" for c in got)


def test_newly_registered_harness_joins_after_explicit_preferences() -> None:
    specs = {**SPECS, "api": _spec("api")}
    statuses = {**ALL_OK, "api": _ok("api")}
    got = plan_candidates(specs, statuses, ["pi", "claude"], ["headless", "pty"])
    assert [c.harness for c in got] == ["pi", "claude", "claude", "api"]


async def test_detector_caches_then_reprobes_after_invalidate() -> None:
    n = {"count": 0}

    def probe() -> HarnessStatus:
        n["count"] += 1
        return HarnessStatus("pi", True, True)

    now = {"t": 0.0}
    spec = HarnessSpec("pi", probe, (H,), lambda m, c: None)
    det = HarnessDetector({"pi": spec}, ttl=60, clock=lambda: now["t"])

    await det.detect()
    await det.detect()
    assert n["count"] == 1
    now["t"] = 61
    await det.detect()
    assert n["count"] == 2
    det.invalidate()
    await det.detect()
    assert n["count"] == 3


async def test_a_raising_probe_degrades_to_unknown_not_a_crash() -> None:
    def boom() -> HarnessStatus:
        raise RuntimeError("kaboom")

    det = HarnessDetector({"pi": HarnessSpec("pi", boom, (H,), lambda m, c: None)})
    status = (await det.detect())["pi"]
    assert status.signed_in is None and status.usable and "kaboom" in status.detail


def test_no_usable_message_names_every_harness_and_the_fix() -> None:
    specs = {
        "pi": _spec("pi", hint="run `pi`, then /login"),
        "claude": _spec("claude", (H, P), hint="run `claude auth login` on the host"),
        "codex": _spec("codex", build=False),
    }
    statuses = {
        "pi": HarnessStatus("pi", False, None, "pi not found"),
        "claude": HarnessStatus("claude", True, False, "Claude Code is logged out"),
        "codex": HarnessStatus("codex", True, True, has_adapter=False),
    }
    msg = str(NoUsableHarnessError(statuses, specs))
    assert "No usable agent harness" in msg
    assert "pi: not installed" in msg
    assert "claude: installed, not signed in — Claude Code is logged out" in msg
    assert "claude auth login" in msg
    assert "codex: installed and signed in, but Hive has no adapter for it yet" in msg


def test_no_usable_message_includes_the_last_error_per_harness() -> None:
    specs = {"claude": _spec("claude", (H, P), hint="run `claude auth login`")}
    err = HarnessError(HarnessErrorKind.AUTH, "claude", H, "Not logged in · Please run /login")
    msg = str(NoUsableHarnessError({"claude": _ok("claude")}, specs, [err]))
    assert "last error: auth — Not logged in" in msg
    assert "claude is signed out — run `claude auth login`" in msg


def test_only_pre_work_refusals_fall_back() -> None:
    for kind in HarnessErrorKind:
        err = HarnessError(kind, "pi", H, "x")
        assert err.falls_back is (kind is not HarnessErrorKind.OTHER)
