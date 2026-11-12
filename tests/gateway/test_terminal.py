"""The Terminal page: firstmate's own herdr pane in the browser, behind the step-up.

Everything here runs against a fake herdr (a script that answers from files and records every
call) and a fake firstmate home. No real herdr server, session or pane is touched.
"""

from __future__ import annotations

import hashlib
import json
import logging
import stat
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hive.gateway import pages, terminal
from hive.gateway.actions import Tokens
from hive.gateway.app import create_app, terminal_csp
from hive.gateway.settings import GatewaySettings
from hive.gateway.terminal import Op

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
ORIGIN = f"https://{HOST}"
GOOD = {"tailscale-user-login": OWNER, "host": HOST}
WS = {**GOOD, "origin": ORIGIN}
POST = {
    **GOOD,
    "origin": ORIGIN,
    "content-type": "application/x-www-form-urlencoded",
    "accept": "application/json",
}
CSRF = Tokens(b"t" * 32)
# What firstmate's pane shows and what the owner types. It must never reach a log or a file.
CANARY = "CANARY-7f3a"
TYPED = "TYPED-91c2"
# sha256 of @xterm/xterm 6.0.0 lib/xterm.js, byte for byte as published on npm.
XTERM_SHA256 = "14903579ff54664cd72f8e8699e6961a6272c21863ec1c3b118cdc8af5d4a972"
PANE = "w1:p1"

FAKE_HERDR = """#!{python} -I
import json, sys
from pathlib import Path
S = Path({state!r})
args = sys.argv[1:]
with open(S / "calls.log", "a") as fh:
    fh.write(json.dumps(args) + "\\n")
if (S / "down").exists():
    sys.exit(1)
if args[:2] == ["pane", "list"]:
    sys.stdout.write((S / "panes.json").read_text())
elif args[:2] == ["pane", "layout"]:
    sys.stdout.write((S / "layout.json").read_text())
elif args[:2] == ["pane", "read"]:
    if (S / "fail_read").exists():
        sys.exit(1)
    source = args[args.index("--source") + 1]
    sys.stdout.write((S / ("history.txt" if source == "recent" else "screen.txt")).read_text())
elif args[:2] in (["pane", "send-text"], ["pane", "send-keys"]):
    if (S / "fail_send").exists():
        sys.exit(1)
    with open(S / "sent.log", "a") as fh:
        fh.write(json.dumps(args) + "\\n")
else:
    sys.exit(2)
"""


def _pane(pane_id: str, cwd: str, fg: str | None, agent: str | None = "claude") -> dict:
    return {
        "pane_id": pane_id,
        "agent": agent,
        "cwd": cwd,
        "foreground_cwd": fg,
        "workspace_id": pane_id.split(":")[0],
        "tab_id": "t1",
        "terminal_id": "x",
        "focused": True,
        "agent_status": "idle",
        "revision": 1,
    }


@dataclass
class World:
    """A fake firstmate home and the fake herdr's files."""

    home: Path
    state: Path
    herdr: Path
    data: Path

    def panes(self, *panes: dict) -> None:
        (self.state / "panes.json").write_text(json.dumps({"result": {"panes": list(panes)}}))

    def firstmate(self, pane_id: str = PANE) -> dict:
        return _pane(pane_id, str(self.home), str(self.home))

    def screen(self, text: str) -> None:
        (self.state / "screen.txt").write_text(text)

    def flag(self, name: str, on: bool = True) -> None:
        path = self.state / name
        if on:
            path.write_text("")
        elif path.exists():
            path.unlink()

    def sent(self) -> list[list[str]]:
        log = self.state / "sent.log"
        return [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []

    def calls(self) -> list[list[str]]:
        log = self.state / "calls.log"
        return [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []

    def audit(self) -> str:
        path = self.data / "terminal-audit.log"
        return path.read_text() if path.exists() else ""

    def settings(self, **kw: object) -> GatewaySettings:
        base: dict[str, object] = {
            "owner_login": OWNER,
            "allowed_hosts": (HOST,),
            "origin": ORIGIN,
            "fm_home": self.home,
            "snapshot_ttl_s": 0,
            "data_dir": self.data,
            "live": False,
            "herdr": self.herdr,
            "fleet_up": None,
            "powershell": None,
            "tailscale": None,
            "systemctl": None,
            "quota_axi": None,
            "rate_limits": None,
            "lavish_state": None,
            "lavish_axi": None,
        }
        base.update(kw)
        return GatewaySettings(**base)  # type: ignore[arg-type]


@pytest.fixture
def world(tmp_path: Path) -> World:
    home = tmp_path / "firstmate"
    (home / "bin").mkdir(parents=True)
    (home / "projects" / "x").mkdir(parents=True)
    snap = home / "bin" / "fm-fleet-snapshot.sh"
    snap.write_text(f"#!/usr/bin/env bash\ncat '{FIXTURE}'\n")
    snap.chmod(snap.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "herdr-state"
    state.mkdir()
    herdr = tmp_path / "herdr"
    herdr.write_text(FAKE_HERDR.format(python=sys.executable, state=str(state)))
    herdr.chmod(herdr.stat().st_mode | stat.S_IXUSR)
    w = World(home, state, herdr, tmp_path / "data")
    w.panes(
        w.firstmate(),
        # A crew opened from the home runs in its worktree: never firstmate's pane.
        _pane("w1:p2", str(home), str(home / "projects" / "x")),
        # A plain shell in the home: not Claude, never firstmate's pane.
        _pane("w2:p1", str(home), str(home), agent=None),
    )
    w.screen(f"\x1b[1;32m{CANARY}\x1b[0m firstmate\n\x1b]0;title\x07> \x1b[2Jready\n")
    (state / "history.txt").write_text(
        "old line 1\nold line 2\n" + f"{CANARY} firstmate\n> ready\n"
    )
    (state / "layout.json").write_text(
        json.dumps({"result": {"panes": [{"pane_id": PANE, "rect": {"width": 90, "height": 3}}]}})
    )
    return w


@pytest.fixture
def make(world: World) -> Iterator[Callable[..., object]]:
    @contextmanager
    def _make(settings: GatewaySettings | None = None, **svc: object) -> Iterator[TestClient]:
        s = settings or world.settings()
        term = terminal.TerminalService(s, **svc) if s.terminal else None  # type: ignore[arg-type]
        app = create_app(s, tokens=CSRF, term=term)
        with TestClient(app, client=("127.0.0.1", 5000)) as c:
            yield c

    yield _make


@pytest.fixture
def client(make: Callable[..., object]) -> Iterator[TestClient]:
    with make() as c:  # type: ignore[attr-defined]
        yield c


def act(c: TestClient, headers: dict[str, str] = POST, **fields: str):
    data = {"csrf": CSRF.csrf(), "next": "/terminal", **fields}
    return c.post("/act/terminal", content=urlencode(data), headers=headers)


def open_pass(c: TestClient) -> str:
    first = act(c)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["confirm"] is True and "pass" not in body
    second = act(c, step=body["step"], rid=body["rid"])
    assert second.status_code == 200, second.text
    return second.json()["pass"]


def until(ws, pred: Callable[[dict], bool], limit: int = 60) -> dict:
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("message never came")


def is_state(**want: object) -> Callable[[dict], bool]:
    return lambda m: m["t"] == "state" and all(m.get(k) == v for k, v in want.items())


def is_t(kind: str) -> Callable[[dict], bool]:
    return lambda m: m["t"] == kind


def attached(ws, token: str) -> dict:
    ws.send_json({"t": "attach", "pass": token})
    return until(ws, is_state(session="ok"))


def take(ws) -> None:
    ws.send_json({"t": "take"})
    confirm = until(ws, is_t("confirm"))
    assert "firstmate" in confirm["detail"]
    ws.send_json({"t": "take", "step": confirm["step"]})
    until(ws, is_state(keyboard="you"))


def wait_for(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("condition never held")


def sync(ws) -> None:
    """Every message sent before this one has been handled: the server answers in order."""
    ws.send_json({"t": "take"})
    until(ws, lambda m: m["t"] in ("confirm", "notice", "state"))


# ---- owner only, HTTP and websocket ------------------------------------------------------


def test_the_page_is_owner_only_and_carries_no_terminal_data(client: TestClient) -> None:
    assert client.get("/terminal").status_code == 403
    assert (
        client.get("/terminal", headers={**GOOD, "tailscale-user-login": "x@y"}).status_code == 403
    )
    assert client.get("/terminal", headers={**GOOD, "host": "evil.example"}).status_code == 403
    page = client.get("/terminal", headers=GOOD)
    assert page.status_code == 200
    assert "Confirm it is you" in page.text and "Open terminal" in page.text
    assert CANARY not in page.text and "\x1b" not in page.text


def test_a_non_owner_never_reaches_the_bridge(make: Callable[..., object], world: World) -> None:
    with make(world.settings()) as c:  # type: ignore[attr-defined]
        bad = [
            {**WS, "tailscale-user-login": "intruder@example.test"},
            {k: v for k, v in WS.items() if k != "tailscale-user-login"},
            {**WS, "host": "evil.example"},
            {**WS, "origin": "https://evil.example"},
            {k: v for k, v in WS.items() if k != "origin"},  # a browser always sends Origin
            {**WS, "origin": "null"},
        ]
        for headers in bad:
            with pytest.raises(WebSocketDisconnect) as refused:
                with c.websocket_connect("/terminal/ws", headers=headers):
                    pass
            assert refused.value.code == 1008, headers
        assert act(c, headers={**POST, "tailscale-user-login": "x@y"}).status_code == 403
        assert act(c, headers={k: v for k, v in POST.items() if k != "origin"}).status_code == 403
    assert not any(call[:2] == ["pane", "read"] for call in world.calls())


def test_a_peer_that_is_not_loopback_is_refused(world: World) -> None:
    s = world.settings()
    app = create_app(s, tokens=CSRF, term=terminal.TerminalService(s))
    with TestClient(app, client=("100.64.0.9", 5000)) as c:
        assert c.get("/terminal", headers=GOOD).status_code == 403
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/terminal/ws", headers=WS):
                pass


def test_the_page_has_its_own_policy_and_the_others_keep_theirs(client: TestClient) -> None:
    csp = client.get("/terminal", headers=GOOD).headers["content-security-policy"]
    assert pages.TERMINAL_SCRIPT_CSP_HASH in csp and pages.SCRIPT_CSP_HASH in csp
    assert f"wss://{HOST}" in csp and "default-src 'none'" in csp
    assert csp == terminal_csp(ORIGIN)
    other = client.get("/machine", headers=GOOD).headers["content-security-policy"]
    assert pages.TERMINAL_SCRIPT_CSP_HASH not in other and "wss:" not in other


# ---- the step-up ---------------------------------------------------------------------------


def test_opening_takes_two_taps_and_a_step_opens_once(client: TestClient, world: World) -> None:
    first = act(client).json()
    assert first == {**first, "ok": True, "confirm": True, "label": "Tap again to open"}
    assert "read-only" in first["detail"]
    second = act(client, step=first["step"], rid=first["rid"]).json()
    assert second["ok"] is True and isinstance(second["pass"], str) and len(second["pass"]) > 30
    assert second["ends"] > time.time() + terminal.SESSION_MAX_S - 60
    again = act(client, step=first["step"], rid=first["rid"]).json()  # a replay: a fresh confirm
    assert again["confirm"] is True and "pass" not in again
    other = act(client).json()  # a step only opens the request it was given for
    swapped = act(client, step=first["step"], rid=other["rid"]).json()
    assert swapped["confirm"] is True and "pass" not in swapped
    forged = act(client, step="9999999999.deadbeef", rid=first["rid"]).json()
    assert forged["confirm"] is True and "pass" not in forged
    assert act(client, csrf="nope").status_code == 403
    html = act(client, headers={**POST, "accept": "text/html"}, step=first["step"])
    assert html.status_code == 400 and "page script" in html.text
    assert world.audit().count("event=open") == 1


def test_a_socket_without_a_good_pass_gets_nothing(client: TestClient, world: World) -> None:
    for hello in ({"t": "attach", "pass": "made-up"}, {"t": "attach"}, {"t": "in", "d": "x"}):
        with client.websocket_connect("/terminal/ws", headers=WS) as ws:
            ws.send_json(hello)
            if hello["t"] == "attach":
                end = ws.receive_json()
                assert end["t"] == "end" and end["reason"] == "step-up"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == terminal.CLOSE_STEP_UP
    assert not any(call[:2] == ["pane", "read"] for call in world.calls())


def test_a_pass_serves_one_live_tab_at_a_time(client: TestClient) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        with client.websocket_connect("/terminal/ws", headers=WS) as copy:
            copy.send_json({"t": "attach", "pass": token})
            assert copy.receive_json()["reason"] == "in-use"
            with pytest.raises(WebSocketDisconnect) as closed:
                copy.receive_json()
            assert closed.value.code == terminal.CLOSE_IN_USE
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:  # a reconnect, once free
        attached(ws, token)


def test_the_session_has_a_hard_time_limit(make: Callable[..., object], world: World) -> None:
    with make(session_max_s=1.5) as c:  # type: ignore[attr-defined]
        token = open_pass(c)
        with c.websocket_connect("/terminal/ws", headers=WS) as ws:
            attached(ws, token)
            take(ws)
            end = until(ws, is_t("end"))
            assert end["reason"] == "expired" and "Confirm it is you" in end["text"]
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == terminal.CLOSE_STEP_UP
        with c.websocket_connect("/terminal/ws", headers=WS) as ws:
            ws.send_json({"t": "attach", "pass": token})
            assert ws.receive_json()["reason"] == "expired"
    log = world.audit()
    assert "event=expired" in log and "event=release" in log and "reason=expired" in log


# ---- the screen ------------------------------------------------------------------------------


def test_the_screen_streams_with_colours_and_nothing_else(client: TestClient) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        state = attached(ws, token)
        assert state["keyboard"] == "none" and state["who"] == OWNER and state["viewers"] == 1
        screen = until(ws, is_t("screen"))
        assert screen["lines"][0] == f"\x1b[1;32m{CANARY}\x1b[0m firstmate"
        assert screen["lines"][1] == "> ready"  # no title, no clear-screen
        assert screen["cols"] == 90  # herdr's layout width, wider than any row
        assert screen["rows"] == 3


def test_history_is_the_scrollback_above_the_screen(client: TestClient) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        ws.send_json({"t": "history"})
        hist = until(ws, is_t("history"))
        assert hist["lines"] == ["old line 1", "old line 2"]
        assert hist["screen"][1] == "> ready"


def test_a_missing_session_is_said_in_plain_words(client: TestClient, world: World) -> None:
    world.panes(_pane("w1:p2", str(world.home), str(world.home / "projects" / "x")))
    res = act(client)
    assert res.status_code == 409 and "Wake firstmate" in res.json()["message"]
    world.panes(world.firstmate("w1:p1"), world.firstmate("w3:p1"))
    res = act(client)
    assert res.status_code == 409 and "will not pick one" in res.json()["message"]
    world.flag("down")
    res = act(client)
    assert res.status_code == 503 and "not answering" in res.json()["message"]


def test_no_herdr_at_all_offers_nothing(make: Callable[..., object], world: World) -> None:
    with make(world.settings(herdr=None)) as c:  # type: ignore[attr-defined]
        res = act(c)
        assert res.status_code == 503 and "not found" in res.json()["message"]
        assert "pass" not in res.json() and "step" not in res.json()


def test_the_session_ending_while_watched_releases_the_keyboard(
    client: TestClient, world: World
) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        take(ws)
        world.panes()
        gone = until(ws, is_state(session="missing"))
        assert gone["keyboard"] == "none" and "Wake firstmate" in gone["why"]
        ws.send_json({"t": "in", "d": f"{TYPED}\r"})
        until(ws, lambda m: m["t"] == "notice" and "Read-only" in m["text"])
        world.panes(world.firstmate("w1:p9"))  # back, in another pane: still read-only
        back = until(ws, is_state(session="ok"))
        assert back["keyboard"] == "none"
    assert world.sent() == []
    assert "reason=session-gone" in world.audit()


def test_a_pane_change_releases_the_keyboard(client: TestClient, world: World) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        take(ws)
        world.panes(world.firstmate("w1:p7"))
        until(ws, lambda m: m["t"] == "notice" and "another pane" in m["text"])
        assert until(ws, is_state())["keyboard"] == "none"
    assert "reason=pane-changed" in world.audit()


# ---- the keyboard ------------------------------------------------------------------------------


def test_read_only_really_drops_input(client: TestClient, world: World) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        ws.send_json({"t": "in", "d": f"{TYPED}\r"})
        ws.send_json({"t": "paste", "d": TYPED, "enter": True})
        note = until(ws, is_t("notice"))
        assert "Read-only" in note["text"] and "Nothing was typed" in note["text"]
        sync(ws)
        time.sleep(0.3)
    assert world.sent() == []


def test_take_type_and_release(client: TestClient, world: World) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        ws.send_json({"t": "take", "step": "guessed"})  # no armed step: refused
        assert "Tap Take keyboard first" in until(ws, is_t("notice"))["text"]
        take(ws)
        ws.send_json({"t": "in", "d": f"{TYPED}\r\x1b[A\x03"})
        wait_for(lambda: len(world.sent()) == 2)
        assert world.sent() == [
            ["pane", "send-text", PANE, "--", TYPED],
            ["pane", "send-keys", PANE, "enter", "up", "ctrl+c"],
        ]
        ws.send_json({"t": "paste", "d": "one\ntwo", "enter": True})
        wait_for(lambda: len(world.sent()) == 4)
        assert world.sent()[2] == ["pane", "send-text", PANE, "--", "\x1b[200~one\ntwo\x1b[201~"]
        assert world.sent()[3] == ["pane", "send-keys", PANE, "enter"]
        ws.send_json({"t": "release"})
        until(ws, is_state(keyboard="none"))
        ws.send_json({"t": "in", "d": "after"})
        until(ws, is_t("notice"))
        time.sleep(0.3)
    assert len(world.sent()) == 4
    log = world.audit()
    assert "event=take" in log and "event=release" in log and "reason=released" in log


def test_one_keyboard_holder_and_another_tab_cannot_type(client: TestClient, world: World) -> None:
    a_token, b_token = open_pass(client), open_pass(client)
    with (
        client.websocket_connect("/terminal/ws", headers=WS) as a,
        client.websocket_connect("/terminal/ws", headers=WS) as b,
    ):
        attached(a, a_token)
        attached(b, b_token)
        take(a)
        assert until(b, is_state(keyboard="other"))["viewers"] == 2
        b.send_json({"t": "take"})
        assert "Another tab has the keyboard" in until(b, is_t("notice"))["text"]
        b.send_json({"t": "in", "d": "from-b\r"})
        b.send_json({"t": "release"})  # not b's to release
        a.send_json({"t": "in", "d": "from-a"})
        wait_for(lambda: len(world.sent()) == 1)
        sync(b)
        time.sleep(0.3)
        assert world.sent() == [["pane", "send-text", PANE, "--", "from-a"]]
    assert "reason=held" in world.audit()


def test_the_keyboard_goes_back_after_idle(make: Callable[..., object], world: World) -> None:
    with make(keyboard_idle_s=0.8) as c:  # type: ignore[attr-defined]
        token = open_pass(c)
        with c.websocket_connect("/terminal/ws", headers=WS) as ws:
            attached(ws, token)
            take(ws)
            note = until(ws, is_t("notice"))
            assert "went back to read-only" in note["text"]
            assert until(ws, is_state())["keyboard"] == "none"
    assert "reason=idle" in world.audit()


def test_closing_the_tab_releases_the_keyboard(client: TestClient, world: World) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        take(ws)
    wait_for(lambda: "event=detach" in world.audit())
    log = world.audit()
    assert "reason=detach" in log
    other = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        assert attached(ws, other)["keyboard"] == "none"


def test_a_refused_send_says_so(client: TestClient, world: World) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        take(ws)
        world.flag("fail_send")
        ws.send_json({"t": "in", "d": "x"})
        assert "did not take" in until(ws, is_t("notice"))["text"]


def test_oversized_input_is_refused_whole(client: TestClient, world: World) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        take(ws)
        ws.send_json({"t": "paste", "d": "x" * (terminal.MAX_INPUT + 1)})
        assert "too much text" in until(ws, is_t("notice"))["text"]
        time.sleep(0.3)
    assert world.sent() == []


# ---- never anything else ------------------------------------------------------------------


def test_the_bridge_only_ever_runs_these_herdr_commands(client: TestClient, world: World) -> None:
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        ws.send_json({"t": "attach", "pass": token, "pane": "w2:p1", "session": "other"})
        until(ws, is_state(session="ok"))
        until(ws, is_t("screen"))
        ws.send_json({"t": "history"})
        until(ws, is_t("history"))
        take(ws)
        ws.send_json({"t": "in", "d": "-rf\r", "pane": "w2:p1"})
        wait_for(lambda: len(world.sent()) == 2)
    allowed = {("pane", "list"), ("pane", "layout"), ("pane", "read")}
    allowed |= {("pane", "send-text"), ("pane", "send-keys")}
    for call in world.calls():
        assert tuple(call[:2]) in allowed, call
        if call[1] != "list":
            assert PANE in call, call  # only ever firstmate's own pane
    assert world.sent()[0] == ["pane", "send-text", PANE, "--", "-rf"]


def test_no_keystroke_or_output_reaches_any_log(
    client: TestClient, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    token = open_pass(client)
    with client.websocket_connect("/terminal/ws", headers=WS) as ws:
        attached(ws, token)
        until(ws, is_t("screen"))
        ws.send_json({"t": "history"})
        until(ws, is_t("history"))
        ws.send_json({"t": "in", "d": TYPED})  # read-only: refused
        take(ws)
        ws.send_json({"t": "in", "d": f"{TYPED}\r"})
        ws.send_json({"t": "paste", "d": f"{TYPED}\nPASTE-2nd-b7"})
        wait_for(lambda: len(world.sent()) == 3)
        world.flag("fail_send")
        ws.send_json({"t": "in", "d": TYPED})
        until(ws, is_t("notice"))
        ws.send_json({"t": "release"})
        until(ws, is_state(keyboard="none"))
    wait_for(lambda: "event=detach" in world.audit())
    logged = "\n".join(r.getMessage() for r in caplog.records)
    files = "\n".join(p.read_text() for p in world.data.rglob("*") if p.is_file())
    for text in (logged, files):
        assert CANARY not in text and TYPED not in text and "PASTE-2nd-b7" not in text
    lines = world.audit().splitlines()
    events = [next(f for f in ln.split() if f.startswith("event=")) for ln in lines]
    assert events == [
        "event=open",
        "event=attach",
        "event=take",
        "event=release",
        "event=detach",
    ]
    for ln in lines:
        assert f"who={OWNER}" in ln and "via=desk" in ln
        for field in ln.split()[1:]:
            assert "=" in field and field.split("=")[0] in {
                "who",
                "via",
                "event",
                "tab",
                "reason",
                "ends",
                "held_s",
                "attached_s",
            }, ln
    assert (world.data / "terminal-audit.log").stat().st_mode & 0o777 == 0o600
    assert "gateway-audit action=terminal" in logged


# ---- limits --------------------------------------------------------------------------------


def test_opens_are_rate_limited_across_restarts(make: Callable[..., object], world: World) -> None:
    with make(open_limit=2) as c:  # type: ignore[attr-defined]
        open_pass(c)
        open_pass(c)
        first = act(c).json()
        res = act(c, step=first["step"], rid=first["rid"])
        assert res.status_code == 429 and "Try again in" in res.json()["message"]
    with make(open_limit=2) as c:  # type: ignore[attr-defined]  # a desk restart: still counted
        first = act(c).json()
        res = act(c, step=first["step"], rid=first["rid"])
        assert res.status_code == 429
    assert "reason=rate-limited" in world.audit()


def test_a_refused_open_is_logged_once(
    make: Callable[..., object], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    with make(open_limit=1) as c:  # type: ignore[attr-defined]
        open_pass(c)
        first = act(c).json()
        caplog.clear()
        res = act(c, step=first["step"], rid=first["rid"])
        assert res.status_code == 429
    refused = [
        r.getMessage()
        for r in caplog.records
        if "action=terminal" in r.getMessage() and "outcome=refused" in r.getMessage()
    ]
    assert refused == [
        "gateway-audit action=terminal subject=- outcome=refused reason=rate-limited"
    ]


def test_attach_attempts_are_rate_limited(make: Callable[..., object]) -> None:
    with make(attach_limit=3) as c:  # type: ignore[attr-defined]
        for _ in range(3):
            with c.websocket_connect("/terminal/ws", headers=WS) as ws:
                ws.send_json({"t": "attach", "pass": "wrong"})
                assert ws.receive_json()["reason"] == "step-up"
        token = open_pass(c)
        with c.websocket_connect("/terminal/ws", headers=WS) as ws:
            ws.send_json({"t": "attach", "pass": token})
            assert ws.receive_json()["reason"] == "rate-limited"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == terminal.CLOSE_LIMIT


def test_the_number_of_tabs_is_capped(client: TestClient) -> None:
    tokens = [open_pass(client) for _ in range(terminal.MAX_VIEWERS + 1)]
    sockets = [client.websocket_connect("/terminal/ws", headers=WS) for _ in tokens]
    opened = [s.__enter__() for s in sockets]
    try:
        for ws, token in zip(opened[:-1], tokens[:-1], strict=True):
            attached(ws, token)
        opened[-1].send_json({"t": "attach", "pass": tokens[-1]})
        assert opened[-1].receive_json()["reason"] == "full"
    finally:
        for s in sockets:
            s.__exit__(None, None, None)


# ---- switched off, vendored files ----------------------------------------------------------


def test_switched_off_offers_nothing(make: Callable[..., object], world: World) -> None:
    with make(world.settings(terminal=False)) as c:  # type: ignore[attr-defined]
        page = c.get("/terminal", headers=GOOD)
        assert page.status_code == 200 and "switched off" in page.text
        assert "term-open" not in page.text and pages.XTERM_JS not in page.text
        assert act(c).status_code == 404
        assert c.get(f"/vendor/{pages.XTERM_JS}", headers=GOOD).status_code == 404
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/terminal/ws", headers=WS):
                pass
        assert "/terminal" not in c.get("/machine", headers=GOOD).text
    assert world.calls() == []


def test_the_emulator_is_the_pinned_local_file(client: TestClient) -> None:
    body = (pages.VENDOR_DIR / pages.XTERM_JS).read_bytes()
    assert hashlib.sha256(body).hexdigest() == XTERM_SHA256
    assert (pages.VENDOR_DIR / "LICENSE-xterm.txt").read_text().startswith("Copyright (c)")
    res = client.get(f"/vendor/{pages.XTERM_JS}", headers=GOOD)
    assert res.status_code == 200 and res.content == body
    assert res.headers["content-type"].startswith("text/javascript")
    assert "immutable" in res.headers["cache-control"]
    assert client.get(f"/vendor/{pages.XTERM_JS}").status_code == 403
    assert client.get("/vendor/xterm.js.map", headers=GOOD).status_code == 404
    page = client.get("/terminal", headers=GOOD).text
    assert f"src='/vendor/{pages.XTERM_JS}' integrity='{pages.XTERM_SRI}'" in page
    assert "cdn" not in page.lower() and "https://" not in page.split("<script src=")[1][:80]


# ---- pure parts -----------------------------------------------------------------------------


def test_only_colours_survive_from_what_the_pane_prints() -> None:
    raw = (
        "\x1b[38;2;10;20;30mrgb\x1b[0m"
        "\x1b]52;c;c2VjcmV0\x07"  # a clipboard write
        "\x1b]0;title\x1b\\"  # a window title
        "\x1b]8;;https://evil.example\x1b\\link\x1b]8;;\x1b\\"  # a hyperlink
        "\x1b[2J\x1b[H\x1b[?1049h\x1b[6n"  # clear, home, alternate screen, cursor report
        "\x1bPdevice\x1b\\\x1b7\x1b8\r\x07\x00tail\x1b"
    )
    assert terminal.sgr_only(raw) == "\x1b[38;2;10;20;30mrgb\x1b[0mlinktail"
    assert terminal.screen_lines("a\r\nb\n\n", 10) == ["a", "b", ""]
    assert terminal.screen_lines("1\n2\n3\n", 2) == ["2", "3"]
    assert terminal.width("\x1b[1mab\x1b[0m界") == 4


def test_typing_becomes_herdr_text_and_keys() -> None:
    t = terminal.translate
    assert t("hi\r") == [Op(text="hi"), Op(keys=("enter",))]
    assert t("\x1b") == [Op(keys=("escape",))]
    assert t("\x1b[A\x1b[B\x1b[C\x1b[D\x1bOA") == [Op(keys=("up", "down", "right", "left", "up"))]
    assert t("\x1b[5~\x1b[6~\x1b[3~\x1b[H\x1b[F\x1b[Z") == [
        Op(keys=("pageup", "pagedown", "delete", "home", "end", "shift+tab"))
    ]
    assert t("\t\x7f\x01\x1a") == [Op(keys=("tab", "backspace", "ctrl+a", "ctrl+z"))]
    # Mouse reports, focus events, modified arrows, function keys and bracketed paste: dropped.
    assert t("\x1b[<0;10;5M\x1b[I\x1b[O\x1b[1;5A\x1b[15~\x1b[200~x\x1b[201~") == [Op(text="x")]
    assert t("é​界") == [Op(text="é​界")]
    for ops in (t(chr(c)) for c in range(0x20)):
        for op in ops:
            assert set(op.keys) <= terminal.KEYS
    assert terminal.paste_text("a\r\nb\x00\x1b[31m") == "\x1b[200~a\nb[31m\x1b[201~"
    assert terminal.paste_text("one line\t") == "one line\t"


def test_firstmate_pane_is_found_by_its_home_alone() -> None:
    home = "/home/u/firstmate"
    fm = _pane("w1:p1", home, home)
    crew = _pane("w1:p2", home, "/home/u/wt/x")
    shell = _pane("w1:p3", home, home, agent=None)
    elsewhere = _pane("w1:p4", "/tmp", "/tmp")
    odd = _pane("-x", home, home)
    assert terminal.find_pane([crew, fm, shell, elsewhere, odd], {home}).pane == terminal.Pane(
        "w1:p1"
    )
    assert terminal.find_pane([crew, shell], {home}).why == "no-session"
    assert terminal.find_pane([fm, _pane("w2:p1", home, None)], {home}).why == "many-sessions"


def test_history_and_screen_overlap_is_counted_once() -> None:
    assert terminal.overlap(["a", "b", "c", "d"], ["c", "d", "e"]) == 2
    assert terminal.overlap(["a", "b"], ["x", "y"]) == 0
    assert terminal.overlap(["a", "\x1b[1mb\x1b[0m "], ["b", "z"]) == 1
    assert terminal.overlap([], ["x"]) == 0
