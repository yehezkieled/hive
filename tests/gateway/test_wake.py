"""Wake firstmate: status rendering, owner-only desk action, rate limit, Telegram allowlist.

No test starts a session: the runner is always a fake, /proc is a temp tree, and the
default runner (which would call scripts/fleet-up.sh) is never reached.
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from hive.gateway.app import create_app
from hive.gateway.settings import GatewaySettings
from hive.gateway.wake import (
    BEAT_FRESH_S,
    FirstmateStatus,
    StatusCache,
    WakeService,
    read_status,
)
from hive.telegram.bridge import WAKE_CALLBACK, TelegramBridge

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
GOOD = {
    "tailscale-user-login": OWNER,
    "host": HOST,
    "origin": f"https://{HOST}",
    "content-type": "application/x-www-form-urlencoded",
}
RID = "web-0123456789abcdef"
NOW = 1_800_000_000.0


def _status(state: str) -> FirstmateStatus:
    session = state != "down"
    beat = {"alive": 20.0, "no-beat": 4000.0, "down": 9 * 3600.0}.get(state)
    return FirstmateStatus(state, session, beat, time.time())


class Fake:
    """A runner that records calls instead of starting anything."""

    def __init__(
        self, code: int = 0, out: str = "fleet-up: firstmate: started in pane w1:p1"
    ) -> None:
        self.calls = 0
        self.code = code
        self.out = out

    async def __call__(self) -> tuple[int, str]:
        self.calls += 1
        return self.code, self.out


def _settings(tmp_path: Path) -> GatewaySettings:
    home = tmp_path / "fm"
    (home / "bin").mkdir(parents=True, exist_ok=True)
    script = home / "bin" / "fm-fleet-snapshot.sh"
    script.write_text(f"#!/usr/bin/env bash\ncat '{FIXTURE}'\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=home,
        snapshot_ttl_s=0,
        data_dir=tmp_path / "data",
        live=False,
        fleet_up=None,
    )


def _service(tmp_path: Path, state: str, runner: Fake, cooldown: float = 60.0) -> WakeService:
    settings = _settings(tmp_path)
    cache = StatusCache(settings, reader=lambda _home: _status(state))
    return WakeService(settings, runner=runner, status=cache, cooldown_s=cooldown)


# ---- status from firstmate's records ------------------------------------------------


def _proc(root: Path, pid: int, comm: str, cwd: Path) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cwd").symlink_to(cwd)


def _home(tmp_path: Path, beat_age: float | None) -> tuple[Path, Path]:
    home = tmp_path / "firstmate"
    (home / "state").mkdir(parents=True)
    if beat_age is not None:
        beat = home / "state" / ".last-watcher-beat"
        beat.touch()
        os.utime(beat, (NOW - beat_age, NOW - beat_age))
    proc = tmp_path / "proc"
    proc.mkdir()
    return home, proc


def test_alive_needs_session_and_fresh_beat(tmp_path: Path) -> None:
    home, proc = _home(tmp_path, 12)
    _proc(proc, 100, "claude", home)
    st = read_status(home, NOW, proc)
    assert (st.state, st.session, st.needs_wake) == ("alive", True, False)
    assert st.beat_age_s == pytest.approx(12)
    assert "session running" in st.describe() and "12s ago" in st.describe()


def test_down_when_no_claude_in_the_firstmate_home(tmp_path: Path) -> None:
    home, proc = _home(tmp_path, 12)
    other = tmp_path / "elsewhere"
    other.mkdir()
    _proc(proc, 100, "claude", other)  # a crew's claude, not firstmate's
    _proc(proc, 101, "bash", home)  # right directory, wrong process
    st = read_status(home, NOW, proc)
    assert (st.state, st.session, st.needs_wake) == ("down", False, True)


def test_silent_watcher_with_a_session_is_not_alive(tmp_path: Path) -> None:
    home, proc = _home(tmp_path, BEAT_FRESH_S + 60)
    _proc(proc, 100, "claude", home)
    assert read_status(home, NOW, proc).state == "no-beat"
    home2, proc2 = _home(tmp_path / "b", None)
    _proc(proc2, 100, "claude", home2)
    st = read_status(home2, NOW, proc2)
    assert st.state == "no-beat" and st.beat_age_s is None
    assert "no watcher beat record" in st.describe()


def test_reading_status_never_writes_firstmates_records(tmp_path: Path) -> None:
    home, proc = _home(tmp_path, 5)
    before = sorted((p.name, p.stat().st_mtime) for p in home.rglob("*"))
    read_status(home, NOW, proc)
    assert before == sorted((p.name, p.stat().st_mtime) for p in home.rglob("*"))


async def test_status_cache_answers_at_once_and_refreshes_behind(tmp_path: Path) -> None:
    reads = 0

    def reader(_home: Path) -> FirstmateStatus:
        nonlocal reads
        reads += 1
        return _status("alive")

    cache = StatusCache(_settings(tmp_path), reader=reader)
    assert cache.peek() is None  # cold: no wait, a refresh is started
    await asyncio.sleep(0.05)
    first = cache.peek()
    assert first is not None and first.state == "alive" and reads == 1


async def test_slow_read_times_out_to_unknown(tmp_path: Path) -> None:
    def slow(_home: Path) -> FirstmateStatus:
        time.sleep(0.5)
        return _status("alive")

    cache = StatusCache(_settings(tmp_path), reader=slow, timeout_s=0.05)
    assert (await cache.fresh()).state == "unknown"


# ---- the action ---------------------------------------------------------------------


async def test_alive_is_an_idempotent_noop(tmp_path: Path) -> None:
    runner = Fake()
    svc = _service(tmp_path, "alive", runner)
    result = await svc.wake("owner", "desk")
    assert (result.outcome, result.ok, runner.calls) == ("noop", True, 0)
    assert not (tmp_path / "data" / "wake-state").exists()  # no cooldown slot used


async def test_down_runs_fleet_up_once_and_audits(tmp_path: Path) -> None:
    runner = Fake()
    svc = _service(tmp_path, "down", runner)
    result = await svc.wake("owner@example.test", "desk")
    assert (result.outcome, runner.calls) == ("started", 1)
    line = svc.audit_path.read_text().strip()
    assert re.match(
        r"^\d{4}-\d\d-\d\dT[\d:]+\S* who=owner@example.test via=desk outcome=started ", line
    )
    assert stat.S_IMODE(svc.audit_path.stat().st_mode) == 0o600


async def test_rate_limit_blocks_a_second_wake(tmp_path: Path) -> None:
    runner = Fake()
    svc = _service(tmp_path, "down", runner)
    assert (await svc.wake("a", "desk")).outcome == "started"
    again = await svc.wake("a", "telegram")
    assert (again.outcome, again.ok, runner.calls) == ("rate-limited", False, 1)
    lines = svc.audit_path.read_text().splitlines()
    assert len(lines) == 2 and "outcome=rate-limited" in lines[1]


async def test_rate_limit_is_shared_across_service_instances(tmp_path: Path) -> None:
    run_a, run_b = Fake(), Fake()
    desk, bot = _service(tmp_path, "down", run_a), _service(tmp_path, "down", run_b)
    assert (await desk.wake("a", "desk")).outcome == "started"
    assert (await bot.wake("b", "telegram")).outcome == "rate-limited"
    assert (run_a.calls, run_b.calls) == (1, 0)


async def test_cooldown_expires(tmp_path: Path) -> None:
    runner = Fake()
    svc = _service(tmp_path, "down", runner, cooldown=0.05)
    await svc.wake("a", "desk")
    await asyncio.sleep(0.1)
    assert (await svc.wake("a", "desk")).outcome == "started"
    assert runner.calls == 2


async def test_failure_is_reported_and_audited(tmp_path: Path) -> None:
    svc = _service(
        tmp_path, "down", Fake(code=1, out="fleet-up: firstmate: herdr server did not come up")
    )
    result = await svc.wake("a", "desk")
    assert (result.outcome, result.ok) == ("failed", False)
    assert "herdr server did not come up" in result.message
    assert "outcome=failed" in svc.audit_path.read_text()


async def test_session_already_running_is_a_noop_not_a_start(tmp_path: Path) -> None:
    out = "fleet-up: firstmate: a claude process already runs in /x; not starting a second"
    result = await _service(tmp_path, "no-beat", Fake(out=out)).wake("a", "desk")
    assert result.outcome == "noop" and result.ok


async def test_missing_script_is_unavailable_and_runs_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    svc = WakeService(settings, status=StatusCache(settings, reader=lambda _h: _status("down")))
    assert not svc.available
    assert (await svc.wake("a", "desk")).outcome == "unavailable"


# ---- the desk ------------------------------------------------------------------------


def _client(tmp_path: Path, state: str, runner: Fake, cooldown: float = 60.0) -> TestClient:
    svc = _service(tmp_path, state, runner, cooldown)
    app = create_app(_settings(tmp_path), wake=svc)
    return TestClient(app, client=("127.0.0.1", 5000))


def _form(client: TestClient, **extra: str) -> dict[str, str]:
    csrf = re.search(r"name=csrf value=\"([0-9a-f]+)\"", client.get("/", headers=GOOD).text)
    assert csrf
    return {"csrf": csrf.group(1), "next": "/", **extra}


def _post(client: TestClient, data: dict[str, str], headers: dict | None = None):
    return client.post("/act/wake", content=urlencode(data), headers=headers or GOOD)


def _confirm(client: TestClient) -> dict[str, str]:
    """Post the button, return the confirm page's fields (the step-up token and request id)."""
    page = _post(client, _form(client))
    assert page.status_code == 200 and "Wake firstmate" in page.text
    return {
        m[1]: m[2]
        for m in re.finditer(r"name=(\w+) value=\"([^\"]*)\"", page.text)
        if m[1] in ("csrf", "rid", "step", "next")
    }


def test_desk_shows_down_with_a_wake_button(tmp_path: Path) -> None:
    # the cache is cold on the first request; a second one shows the refreshed state
    c = _client(tmp_path, "down", Fake())
    c.get("/", headers=GOOD)
    time.sleep(0.1)
    page = c.get("/", headers=GOOD).text
    assert "firstmate · down" in page and "no session process" in page
    assert "action='/act/wake'" in page and "Wake firstmate</button>" in page


def test_desk_shows_alive_without_a_button(tmp_path: Path) -> None:
    c = _client(tmp_path, "alive", Fake())
    c.get("/", headers=GOOD)
    time.sleep(0.1)
    page = c.get("/", headers=GOOD).text
    assert "firstmate · alive" in page and "session running" in page
    assert "/act/wake" not in page


def test_first_post_only_asks_to_confirm(tmp_path: Path) -> None:
    runner = Fake()
    c = _client(tmp_path, "down", runner)
    fields = _confirm(c)
    assert "step" in fields and runner.calls == 0


def test_confirmed_post_runs_once_and_shows_the_result_inline(tmp_path: Path) -> None:
    runner = Fake()
    c = _client(tmp_path, "down", runner)
    fields = _confirm(c)
    res = _post(c, fields)
    assert res.status_code == 200 and "started in pane" in res.text and runner.calls == 1
    assert _post(c, fields).status_code == 200  # a double-submit replays, never re-runs
    assert runner.calls == 1


def test_second_wake_within_the_minute_is_429(tmp_path: Path) -> None:
    runner = Fake()
    c = _client(tmp_path, "down", runner)
    assert _post(c, _confirm(c)).status_code == 200
    again = _post(c, _confirm(c))
    assert again.status_code == 429 and runner.calls == 1


def test_alive_confirmed_post_is_a_noop_200(tmp_path: Path) -> None:
    runner = Fake()
    c = _client(tmp_path, "alive", runner)
    res = _post(c, _confirm(c))
    assert res.status_code == 200 and "already alive" in res.text and runner.calls == 0


def test_a_step_token_for_another_request_is_not_accepted(tmp_path: Path) -> None:
    runner = Fake()
    c = _client(tmp_path, "down", runner)
    fields = _confirm(c)
    fields["rid"] = "web-ffffffffffffffff"
    res = _post(c, fields)
    assert "step" in _confirm_page_fields(res.text) and runner.calls == 0


def _confirm_page_fields(html: str) -> set[str]:
    return set(re.findall(r"name=(\w+) value=", html))


def test_wake_works_while_the_snapshot_is_unreadable(tmp_path: Path) -> None:
    runner = Fake()
    settings = _settings(tmp_path)
    (settings.fm_home / "bin" / "fm-fleet-snapshot.sh").write_text("#!/usr/bin/env bash\nexit 3\n")
    svc = WakeService(
        settings,
        runner=runner,
        status=StatusCache(settings, reader=lambda _h: _status("down")),
    )
    c = TestClient(create_app(settings, wake=svc), client=("127.0.0.1", 5000))
    assert "Read-only fallback" in c.get("/", headers=GOOD).text
    assert _post(c, _confirm(c)).status_code == 200 and runner.calls == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"host": HOST, "origin": f"https://{HOST}"},  # no login
        {**GOOD, "tailscale-user-login": "intruder@example.test"},
        {**GOOD, "origin": "https://evil.example"},
        {**GOOD, "host": "evil.example"},
        {k: v for k, v in GOOD.items() if k != "origin"},  # no Origin
        {**GOOD, "sec-fetch-site": "cross-site"},
    ],
)
def test_only_the_owner_can_wake_and_nothing_runs_otherwise(tmp_path: Path, headers: dict) -> None:
    runner = Fake()
    c = _client(tmp_path, "down", runner)
    data = {**_confirm(c)}
    assert _post(c, data, headers).status_code == 403
    assert runner.calls == 0


def test_non_loopback_peer_cannot_wake(tmp_path: Path) -> None:
    runner = Fake()
    c = _client(tmp_path, "down", runner)
    data = _confirm(c)
    remote = TestClient(c.app, client=("100.64.0.9", 5000))
    assert _post(remote, data).status_code == 403 and runner.calls == 0


def test_wake_needs_the_csrf_token(tmp_path: Path) -> None:
    runner = Fake()
    c = _client(tmp_path, "down", runner)
    fields = _confirm(c)
    assert _post(c, {**fields, "csrf": "0" * 64}).status_code == 403
    assert _post(c, {k: v for k, v in fields.items() if k != "csrf"}).status_code == 403
    assert runner.calls == 0


def test_wake_cannot_be_triggered_by_get(tmp_path: Path) -> None:
    runner = Fake()
    c = _client(tmp_path, "down", runner)
    assert c.get("/act/wake", headers=GOOD).status_code in (404, 405)
    assert runner.calls == 0


# ---- Telegram ------------------------------------------------------------------------


class _Msg:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text: str, reply_markup: object = None) -> None:
        self.replies.append((text, reply_markup))


class _Query:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = _Msg()
        self.answers: list[str] = []

    async def answer(self, text: str = "") -> None:
        self.answers.append(text)


def _bridge(tmp_path: Path, state: str, runner: Fake, allowed: list[int]) -> TelegramBridge:
    bridge = TelegramBridge.__new__(TelegramBridge)
    bridge.allowed_user_ids = allowed
    bridge.wake = _service(tmp_path, state, runner)
    return bridge


def _message_update(user_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(message=_Msg(text), effective_user=SimpleNamespace(id=user_id))


async def test_telegram_wake_shows_status_and_a_button_for_the_allowlisted(tmp_path: Path) -> None:
    runner = Fake()
    bridge = _bridge(tmp_path, "down", runner, [42])
    update = _message_update(42, "/wake")
    await bridge._handle_message(update, None)  # type: ignore[arg-type]
    ((text, markup),) = update.message.replies
    assert "down" in text and markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.callback_data == WAKE_CALLBACK and runner.calls == 0


async def test_telegram_alive_has_no_button(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path, "alive", Fake(), [42])
    update = _message_update(42, "/wake")
    await bridge._handle_message(update, None)  # type: ignore[arg-type]
    ((text, markup),) = update.message.replies
    assert "alive" in text and markup is None


@pytest.mark.parametrize("allowed", [[7], []])  # not listed, and the empty-allowlist case
async def test_telegram_strangers_get_nothing_from_wake(tmp_path: Path, allowed: list[int]) -> None:
    runner = Fake()
    bridge = _bridge(tmp_path, "down", runner, allowed)
    update = _message_update(42, "/wake")
    await bridge._handle_message(update, None)  # type: ignore[arg-type]
    assert update.message.replies == [] and runner.calls == 0


async def test_telegram_button_runs_the_wake_for_the_allowlisted(tmp_path: Path) -> None:
    runner = Fake()
    bridge = _bridge(tmp_path, "down", runner, [42])
    query = _Query(WAKE_CALLBACK)
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=42))
    await bridge._handle_callback(update, None)  # type: ignore[arg-type]
    assert runner.calls == 1 and "started" in query.message.replies[0][0]
    assert "who=telegram:42 via=telegram" in bridge.wake.audit_path.read_text()


@pytest.mark.parametrize("allowed", [[7], []])
async def test_telegram_button_from_a_stranger_has_no_effect(
    tmp_path: Path, allowed: list[int]
) -> None:
    runner = Fake()
    bridge = _bridge(tmp_path, "down", runner, allowed)
    query = _Query(WAKE_CALLBACK)
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=42))
    await bridge._handle_callback(update, None)  # type: ignore[arg-type]
    assert runner.calls == 0 and query.answers == [] and query.message.replies == []
    assert not bridge.wake.audit_path.exists()


async def test_telegram_second_tap_is_rate_limited(tmp_path: Path) -> None:
    runner = Fake()
    bridge = _bridge(tmp_path, "down", runner, [42])
    for _ in range(2):
        query = _Query(WAKE_CALLBACK)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=42))
        await bridge._handle_callback(update, None)  # type: ignore[arg-type]
    assert runner.calls == 1 and "rate-limited" in query.message.replies[0][0]
