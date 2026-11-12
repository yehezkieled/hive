"""The Run page: fixed fix buttons behind the step-up, logged, with fakes only.

Nothing here runs a real ``systemctl``, fleet-up, ``schtasks.exe`` or admin helper. ``Host``
answers for all of them; it plays the Windows admin helper by the helper's own allow-list
and request grammar, parsed from ``deploy/windows/HiveAdminHelper.ps1``, and it refuses
(and records) any argument list that is not one of the few the Run page may ever run.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import html
import json
import logging
import os
import re
import stat
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from hive.gateway import runqueue
from hive.gateway.actions import ActionError, Tokens
from hive.gateway.app import RUN_PENDING, create_app
from hive.gateway.runqueue import ACTIONS, Done, RunQueue
from hive.gateway.settings import GatewaySettings
from tests.gateway.fmcfg import write_config
from tests.gateway.test_stack_home import FAKE_LIVE, _in_chrome, needs_chrome

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
PS1 = Path(__file__).parents[2] / "deploy" / "windows" / "HiveAdminHelper.ps1"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
GOOD = {
    "tailscale-user-login": OWNER,
    "host": HOST,
    "origin": f"https://{HOST}",
    "content-type": "application/x-www-form-urlencoded",
    "accept": "application/json",
}
PAGE = {"tailscale-user-login": OWNER, "host": HOST}
TOKENS = Tokens(b"r" * 32)
SECRET = "sk-ant-oat01-PLANTEDsecretPLANTEDsecretPLANTED"
TASK = r"\Hive\Hive Admin Helper"
LINUX = [k for k, a in ACTIONS.items() if a.side == "linux"]
WINDOWS = [k for k, a in ACTIONS.items() if a.side == "windows"]


def _block(name: str) -> str:
    text = PS1.read_text()
    return text.split(f"# BEGIN {name}")[1].split(f"# END {name}")[0]


# The helper's own rules, from its script: verb -> argument rule, the request pattern, limits.
ALLOW = dict(re.findall(r"^\s*'([a-z-]+)'\s*=\s*'([a-z-]+)'", _block("ALLOW-LIST"), re.M))
GRAMMAR = re.search(r"HiveAdminRequestPattern = '([^']+)'", _block("REQUEST-GRAMMAR"))[1]
LIMITS = {
    k: int(v) for k, v in re.findall(r"\$script:HiveAdmin(\w+) = (\d+)", _block("REQUEST-GRAMMAR"))
}
REPLIES = {
    "ping": "pong",
    "restart-wsl": "restarted service WSLService; keep-alive restarts the distro",
    "restart-tailscale": "restarted service Tailscale",
    "tailscale-up": "tailscale up ok:",
    "restart-pc": "restart scheduled in 30 s",
    "pause-updates": "updates paused until 2026-11-19T00:00:00Z",
}


class Host:
    """The fake PC: systemctl, fleet-up, and schtasks.exe running the admin helper."""

    def __init__(self, tmp: Path) -> None:
        self.bin = tmp / "bin"
        self.bin.mkdir()
        for name in ("systemctl", "fleet-up.sh", "schtasks.exe"):
            (self.bin / name).write_text("never run: the tests answer for it\n")
        self.root = tmp / "HiveAdmin"
        (self.root / "request").mkdir(parents=True)
        (self.root / "audit.log").write_bytes(b"")
        self.calls: list[tuple[str, ...]] = []
        self.bad: list[tuple[str, ...]] = []  # anything not on the list; must stay empty
        self.requests: list[str] = []  # request lines the helper accepted
        self.mainpid = os.getpid()
        self.active = "active"
        self.hang: set[str] = set()  # systemctl verbs (or "serve") that never finish
        self.slow = 0.0
        self.fail_with = ""  # restart/start fail with this output
        self.serve = "serve: :8446 -> http://127.0.0.1:8480\n"
        self.helper = "answer"  # answer, silent, started, fail, reject, denied, no-task
        self.helper_slow = 0.0
        self.helper_detail = ""

    @property
    def allowed(self) -> set[tuple[str, ...]]:
        s, f, t = (str(self.bin / n) for n in ("systemctl", "fleet-up.sh", "schtasks.exe"))
        return {
            (s, "--user", "show", "--property=MainPID", "--value", "hive-gateway.service"),
            (s, "--user", "restart", "--no-block", "hive-gateway.service"),
            (s, "--user", "restart", "clip-desk.service"),
            (s, "--user", "restart", "hive-telegram.service"),
            (s, "--user", "is-active", "clip-desk.service"),
            (s, "--user", "is-active", "hive-telegram.service"),
            (s, "--user", "start", "hive-fleet-up.service"),
            (f, "--only", "serve"),
            (t, "/run", "/tn", TASK),
        }

    async def __call__(self, argv: Sequence[str], timeout_s: float) -> Done:
        argv = tuple(argv)
        self.calls.append(argv)
        if argv not in self.allowed:
            self.bad.append(argv)
            return Done(99, "not on the list")
        prog = Path(argv[0]).name
        if prog == "schtasks.exe":
            return await self._schtasks(timeout_s)
        verb = argv[2] if prog == "systemctl" else "serve"
        if verb in self.hang:
            await asyncio.sleep(timeout_s)
            return Done(-9, "", True)
        if self.slow and verb not in ("show", "is-active"):
            await asyncio.sleep(self.slow)
        if verb == "show":
            return Done(0, f"{self.mainpid}\n")
        if verb == "is-active":
            return Done(0 if self.active == "active" else 3, f"{self.active}\n")
        if verb == "serve":
            return Done(0, self.serve)
        if self.fail_with:
            return Done(1, self.fail_with)
        return Done(0, "")

    async def _schtasks(self, timeout_s: float) -> Done:
        if self.helper == "denied":
            return Done(1, "ERROR: Access is denied.\r\n")
        if self.helper == "no-task":
            return Done(1, "ERROR: The system cannot find the file specified.\r\n")
        if self.helper_slow:
            await asyncio.sleep(self.helper_slow)
        if self.helper != "silent":
            asyncio.get_running_loop().call_later(0.05, self.helper_runs)
        return Done(0, f'SUCCESS: Attempted to run the scheduled task "{TASK}".\r\n')

    def audit(self, nonce: str, verb: str, arg: str, result: str, detail: str) -> None:
        line = (
            f"2026-11-12T10:00:00+11:00 nonce={nonce} verb={verb} arg={arg} "
            f"result={result} detail={detail}\r\n"
        )
        with open(self.root / "audit.log", "ab") as fh:
            fh.write(line.encode("ascii"))

    def helper_runs(self) -> None:
        """What HiveAdminHelper.ps1 does with one request, by its own rules."""
        req = self.root / "request" / "request.txt"
        if not req.exists():
            return self.audit("-", "-", "-", "ignored", "no request file")
        data = req.read_bytes()
        req.unlink()  # consumed before it is validated
        text = data.removesuffix(b"\n").decode("ascii", "replace")
        m = re.fullmatch(GRAMMAR, text)
        if self.helper == "reject" or len(data) > LIMITS["MaxRequestBytes"] or not m:
            return self.audit("-", "-", "-", "rejected", "rejected: unknown verb")
        if m[2] not in ALLOW:
            return self.audit("-", "-", "-", "rejected", "rejected: unknown verb")
        nonce, verb, arg = m[1], m[2], m[3] or "-"
        self.requests.append(text)
        self.audit(nonce, verb, arg, "started", "")
        if self.helper == "started":
            return None
        if self.helper == "fail":
            return self.audit(nonce, verb, arg, "failed", "no WSL service found")
        return self.audit(nonce, verb, arg, "ok", self.helper_detail or REPLIES[verb])


def _settings(tmp: Path, host: Host, **changes: object) -> GatewaySettings:
    home = tmp / "fm"
    (home / "bin").mkdir(parents=True)
    snap = home / "bin" / "fm-fleet-snapshot.sh"
    snap.write_text(f"#!/usr/bin/env bash\ncat '{FIXTURE}'\n")
    snap.chmod(snap.stat().st_mode | stat.S_IXUSR)
    write_config(home)
    base = {
        "owner_login": OWNER,
        "allowed_hosts": (HOST,),
        "fm_home": home,
        "data_dir": tmp / "data",
        "live": False,
        "systemctl": host.bin / "systemctl",
        "fleet_up": host.bin / "fleet-up.sh",
        "admin_helper_root": host.root,
        "schtasks": host.bin / "schtasks.exe",
    }
    return GatewaySettings(**{**base, **changes})


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runqueue, "POLL_S", 0.02)


def short(monkeypatch: pytest.MonkeyPatch, key: str, timeout_s: float) -> None:
    """Give one action a short hard time-out, for the time-out tests."""
    monkeypatch.setitem(ACTIONS, key, dataclasses.replace(ACTIONS[key], timeout_s=timeout_s))


@contextlib.contextmanager
def _desk(tmp: Path, **changes: object) -> Iterator[SimpleNamespace]:
    host = Host(tmp)
    settings = _settings(tmp, host, **changes)
    rq = RunQueue(settings, run=host)
    app = create_app(settings, tokens=TOKENS, runq=rq)
    with TestClient(app, client=("127.0.0.1", 5000), follow_redirects=False) as c:
        yield SimpleNamespace(
            c=c, host=host, rq=rq, settings=settings, log=tmp / "data" / runqueue.LOG_NAME
        )
    assert host.bad == []  # nothing off the list was ever run


@pytest.fixture
def desk(tmp_path: Path) -> Iterator[SimpleNamespace]:
    with _desk(tmp_path) as d:
        yield d


def post(c: TestClient, headers: dict[str, str] = GOOD, csrf: str | None = None, **fields: str):
    data = {"csrf": TOKENS.csrf() if csrf is None else csrf, "next": "/run", **fields}
    return c.post("/act/run", content=urlencode(data), headers=headers)


def tap(c: TestClient, key: str, n: int | None = None) -> list:
    """The owner's taps, as the page sends them: the first, each confirm, then the run
    asked again with the same request id while it answers pending."""
    fields = {"do": key, **({"n": str(n)} if n is not None else {})}
    replies = []
    for _ in range(3):
        r = post(c, **fields)
        replies.append(r)
        j = r.json()
        if not j.get("confirm"):
            break
        fields = {**j["extra"], "rid": j["rid"], "step": j["step"]}
    deadline = time.monotonic() + 15
    while replies[-1].status_code == 202 and time.monotonic() < deadline:
        time.sleep(0.1)
        replies.append(post(c, **fields))
    return replies


def page(c: TestClient) -> str:
    r = c.get("/run", headers=PAGE)
    assert r.status_code == 200
    return r.text


def logged(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _rid(n: int) -> str:
    return f"web-{n:016x}"


# ---- only the listed actions run ----------------------------------------------------------


def test_each_linux_button_runs_exactly_its_listed_commands(desk: SimpleNamespace) -> None:
    s, f = str(desk.host.bin / "systemctl"), str(desk.host.bin / "fleet-up.sh")
    expected = {
        "restart-desk": [
            (s, "--user", "show", "--property=MainPID", "--value", "hive-gateway.service"),
            (s, "--user", "restart", "--no-block", "hive-gateway.service"),
        ],
        "restart-clipdesk": [
            (s, "--user", "restart", "clip-desk.service"),
            (s, "--user", "is-active", "clip-desk.service"),
        ],
        "restart-telegram": [
            (s, "--user", "restart", "hive-telegram.service"),
            (s, "--user", "is-active", "hive-telegram.service"),
        ],
        "fleet-up": [(s, "--user", "start", "hive-fleet-up.service")],
        "serve-maps": [(f, "--only", "serve")],
    }
    assert set(expected) == set(LINUX)
    for key, commands in expected.items():
        desk.host.calls.clear()
        last = tap(desk.c, key)[-1]
        assert last.status_code == 200 and last.json()["ok"] is True, (key, last.text)
        assert desk.host.calls == commands, key


def test_each_windows_button_sends_one_request_line_with_the_helpers_own_verb(
    desk: SimpleNamespace,
) -> None:
    schtasks = (str(desk.host.bin / "schtasks.exe"), "/run", "/tn", TASK)
    for key in WINDOWS:
        a = ACTIONS[key]
        desk.host.calls.clear()
        last = tap(desk.c, key, a.default)[-1]
        assert last.status_code == 200, (key, last.text)
        assert last.json()["message"].startswith("The admin helper did it: ")
        assert desk.host.calls == [schtasks]  # nothing but the helper's own task
        nonce, verb, *num = desk.host.requests[-1].split(" ")
        assert re.fullmatch(r"hive-[0-9a-f]{16}", nonce)
        assert verb == a.verb and num == ([] if a.default is None else [str(a.default)])
    assert len({r.split(" ")[0] for r in desk.host.requests}) == len(WINDOWS)  # fresh nonces
    assert not (desk.host.root / "request" / "request.txt").exists()


def test_every_windows_button_is_a_verb_the_helper_already_defines() -> None:
    rules = {"days": "days-optional"}
    assert {ACTIONS[k].verb for k in WINDOWS} == set(ALLOW)  # every helper verb, nothing else
    for key in WINDOWS:
        a = ACTIONS[key]
        assert a.verb in ALLOW, key
        if a.choice:
            assert ALLOW[a.verb] == rules[a.choice], key
        assert all(LIMITS["DaysMin"] <= v <= LIMITS["DaysMax"] for v in a.choices), key
        for n in a.choices or (None,):
            line = runqueue.request_line(runqueue.new_nonce(), a.verb, n)
            assert len(line) <= LIMITS["MaxRequestBytes"]
            assert re.fullmatch(GRAMMAR, line.decode("ascii").removesuffix("\n")), line
    assert runqueue.DEFAULT_DAYS == LIMITS["DaysDefault"]
    # the helper has no cancel verb, so the desk has no cancel button, and no restart set for
    # later that could not be cancelled: a PC restart never carries an hour. It says so.
    assert not any("cancel" in v for v in ALLOW)
    assert [a.choice for a in ACTIONS.values() if a.verb == "restart-pc"] == [""]
    with pytest.raises(ValueError):
        runqueue.request_line(runqueue.new_nonce(), "restart-pc", 3)
    assert [label for label, _ in runqueue.NOT_OFFERED] == [
        "Cancel a scheduled restart",
        "Restart the PC at a set hour",
    ]


def test_an_unknown_action_is_refused_and_nothing_runs(desk: SimpleNamespace) -> None:
    for do in (
        "",
        "reboot",
        "restart_desk",
        "Restart-Desk",
        "ping",
        "restart-pc-now",
        "restart-pc-at",
    ):
        r = post(desk.c, do=do)
        assert r.status_code == 400 and r.json()["ok"] is False
        assert "not one of the Run page's actions" in r.json()["message"]
    assert desk.host.calls == []


# ---- the step-up --------------------------------------------------------------------------


def test_nothing_runs_without_the_step_up_for_exactly_that_action(desk: SimpleNamespace) -> None:
    c = desk.c
    first = post(c, do="restart-clipdesk").json()
    assert first["confirm"] is True and first["label"] == "Tap again to run"
    assert "Exactly: systemctl --user restart clip-desk.service." in first["detail"]
    assert "Time-out 45 s." in first["detail"]
    rid = first["rid"]
    assert re.fullmatch(r"web-[0-9a-f]{16}", rid)  # the desk's own request id
    wrong = [
        "",
        "garbage",
        "9999999999.abc",
        TOKENS.step_up("run", f"restart-telegram:-:{rid}"),  # another action
        TOKENS.step_up("run", f"restart-clipdesk:3:{rid}"),  # a number it does not take
        TOKENS.step_up("run", f"restart-clipdesk:-:{_rid(1)}"),  # another request
        TOKENS.step_up("merge", f"restart-clipdesk:-:{rid}"),  # another kind of step-up
        TOKENS.step_up("run-down", f"restart-clipdesk:-:{rid}"),
        TOKENS.step_up("run", f"restart-clipdesk:-:{rid}", now=time.time() - 600),  # expired
        Tokens(b"z" * 32).step_up("run", f"restart-clipdesk:-:{rid}"),  # another key
    ]
    for step in wrong:
        again = post(c, do="restart-clipdesk", rid=rid, step=step).json()
        assert again["confirm"] is True and again["rid"] != rid, step  # back to the start
    assert desk.host.calls == []
    # the right step for another request id does not pass either
    assert post(c, do="restart-clipdesk", rid=_rid(1), step=first["step"]).json()["confirm"]
    assert desk.host.calls == []
    done = post(c, do="restart-clipdesk", rid=rid, step=first["step"]).json()
    assert done["ok"] is True and "confirm" not in done
    assert len(desk.host.calls) == 2


def test_the_same_confirmed_tap_sent_twice_runs_once(desk: SimpleNamespace) -> None:
    first = post(desk.c, do="fleet-up").json()
    fields = {**first["extra"], "rid": first["rid"], "step": first["step"]}
    a, b = post(desk.c, **fields), post(desk.c, **fields)
    assert a.json() == b.json() and a.status_code == b.status_code == 200
    assert len(desk.host.calls) == 1
    assert len([r for r in logged(desk.log) if r["result"] != "running"]) == 1


def test_a_pc_restart_needs_a_second_confirm_that_says_what_goes_down(
    desk: SimpleNamespace,
) -> None:
    c = desk.c
    one = post(c, do="restart-pc").json()
    assert one["confirm"] is True and one["label"] == "Tap again: next step"
    assert "Exactly: admin helper verb: restart-pc." in one["detail"]
    fields = {**one["extra"], "rid": one["rid"], "step": one["step"]}
    assert one["extra"] == {"do": "restart-pc"}
    two = post(c, **fields).json()
    assert two["confirm"] is True and two["label"] == "Yes, restart the PC now"
    for words in (
        "The PC restarts in 30 seconds.",
        "Everything goes down: Windows, Tailscale, WSL, this desk, firstmate and every worker",
        "The PC comes back by itself",
        "within 5 to 12 minutes",
        "not back after 15 minutes, someone has to go to the PC",
        "Once you confirm, the desk cannot stop it.",
    ):
        assert words in two["detail"], words
    assert desk.host.calls == []  # two confirms in, still nothing ran
    # the first step again only shows the second confirm again
    assert post(c, **fields).json()["confirm"] is True
    # the second step with an hour added is refused: the restart takes no number
    hour = post(c, **{**two["extra"], "n": "3", "rid": two["rid"], "step": two["step"]})
    assert hour.status_code == 400 and hour.json()["ok"] is False
    # the second step's token is no confirm for another action: that one starts over
    other = post(c, do="restart-wsl", rid=two["rid"], step=two["step"]).json()
    assert other["confirm"] is True and other["rid"] != two["rid"]
    assert desk.host.calls == []
    done = post(c, **{**two["extra"], "rid": two["rid"], "step": two["step"]})
    assert done.status_code == 200 and done.json()["ok"] is True, done.text
    [line] = desk.host.requests
    assert re.fullmatch(r"hive-[0-9a-f]{16} restart-pc", line)  # now, never at an hour


@pytest.mark.parametrize("key", ["restart-pc", "restart-wsl"])
def test_the_other_down_actions_need_the_second_confirm_too(
    desk: SimpleNamespace, key: str
) -> None:
    replies = tap(desk.c, key)
    assert [bool(r.json().get("confirm")) for r in replies] == [True, True, False]
    assert "comes back by itself" in replies[1].json()["detail"]
    assert replies[-1].json()["ok"] is True


def test_a_plain_action_has_one_confirm(desk: SimpleNamespace) -> None:
    replies = tap(desk.c, "pause-updates", 14)
    assert [bool(r.json().get("confirm")) for r in replies] == [True, False]
    assert "for 14 days from now" in replies[0].json()["detail"]
    assert desk.host.requests[0].endswith(" pause-updates 14")


def test_without_the_page_script_the_confirm_is_a_page_with_a_form(desk: SimpleNamespace) -> None:
    headers = {k: v for k, v in GOOD.items() if k != "accept"}
    r = post(desk.c, headers=headers, do="helper-check")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Exactly: admin helper verb: ping." in r.text
    assert "action='/act/run'" in r.text and 'name=step value="' in r.text
    assert desk.host.calls == []


# ---- no link, GET or other page; no shell ---------------------------------------------------


def test_a_link_a_get_or_another_page_cannot_run_anything(desk: SimpleNamespace) -> None:
    c = desk.c
    first = post(c, do="helper-check").json()
    fields = {"do": "helper-check", "rid": first["rid"], "step": first["step"]}
    assert c.get(f"/act/run?{urlencode(fields)}", headers=PAGE).status_code == 405
    assert c.get(f"/run?{urlencode(fields)}", headers=PAGE).status_code == 200
    no_origin = {k: v for k, v in GOOD.items() if k != "origin"}
    for headers, csrf in (
        ({**GOOD, "origin": "https://evil.example"}, None),  # another site's page
        (no_origin, None),
        (GOOD, ""),  # no CSRF token
        (GOOD, "wrong"),
        ({**GOOD, "tailscale-user-login": "someone@else.test"}, None),  # not the owner
    ):
        r = post(c, headers=headers, csrf=csrf, **fields)
        assert r.status_code in (401, 403), (headers, csrf, r.status_code)
    for _ in range(3):
        page(c)  # showing the page runs nothing
    assert desk.host.calls == [] and desk.host.requests == []


PAYLOADS = [
    "; touch /tmp/hive-pwned",
    "$(touch hive-pwned)",
    "`id`",
    "3; shutdown /r",
    "3\nhive-aaaaaaaaaaaa restart-wsl",
    "3 ",
    " 3",
    "03",
    "+3",
    "-1",
    "24",
    "36",
    "3.0",
    "３",  # a full-width digit 3
    "0x3",
    "restart-pc 3",
    "../../etc/passwd",
    "%0a",
    "\x00",
    "ping\r\nhive-aaaaaaaa restart-pc",
]


def test_no_text_from_any_field_reaches_a_command_or_the_request_file(
    desk: SimpleNamespace,
) -> None:
    c = desk.c
    for p in PAYLOADS:
        for fields in (
            {"do": p},
            {"do": f"restart-wsl{p}"},
            {"do": "restart-pc", "n": p},
            {"do": "pause-updates", "n": p},
            {"do": "helper-check", "n": p},
        ):
            r = post(c, **fields)
            assert r.status_code == 400 and r.json()["ok"] is False, (fields, r.text)
        # a request id or step that is not the desk's own only starts the confirm again
        r = post(c, do="helper-check", rid=p, step=TOKENS.step_up("run", f"helper-check:-:{p}"))
        assert r.json()["confirm"] is True
    assert desk.host.calls == []
    # fields the Run page does not have are ignored, all the way to the request line
    extra = {"verb": "restart-pc", "nonce": "x" * 10, "arg": "3", "cmd": "rm -rf /", "argv": "sh"}
    first = post(c, do="helper-check", **extra).json()
    assert "admin helper verb: ping." in first["detail"]
    done = post(c, do="helper-check", rid=first["rid"], step=first["step"], **extra).json()
    assert done["ok"] is True
    [line] = desk.host.requests
    assert re.fullmatch(r"hive-[0-9a-f]{16} ping", line)
    assert not Path("/tmp/hive-pwned").exists()


def test_the_request_line_is_built_only_from_listed_values() -> None:
    with pytest.raises(ValueError):
        runqueue.request_line("hive-0123456789abcdef", "restart-pc; ping", None)
    with pytest.raises(ValueError):
        runqueue.request_line("bad nonce", "ping", None)
    with pytest.raises(ValueError):
        runqueue.request_line("x", "ping", None)
    for verb, n in (("restart-pc", 7), ("pause-updates", 2), ("pause-updates", None)):
        with pytest.raises(ValueError):  # a number no button offers for that verb, or none
            runqueue.request_line("hive-0123456789abcdef", verb, n)
    assert runqueue.request_line("hive-0123456789abcdef", "pause-updates", 7) == (
        b"hive-0123456789abcdef pause-updates 7\n"
    )
    assert runqueue.request_line("hive-0123456789abcdef", "restart-pc", None) == (
        b"hive-0123456789abcdef restart-pc\n"
    )
    for form in (
        {"do": "pause-updates"},
        {"do": "pause-updates", "n": "07"},
        {"do": "restart-pc", "n": "3"},
        {"do": "restart-pc-at", "n": "3"},
    ):
        with pytest.raises(ActionError):
            runqueue.parse(form)
    assert runqueue.parse({"do": "pause-updates", "n": "1"}) == (ACTIONS["pause-updates"], 1)
    assert runqueue.parse({"do": "restart-pc"}) == (ACTIONS["restart-pc"], None)


# ---- the helper absent, silent or refusing ---------------------------------------------------


def _offered(html_text: str) -> set[str]:
    return set(re.findall(r'<input type=hidden name=do value="([a-z-]+)">', html_text))


@pytest.mark.parametrize("how", ["not configured", "no request folder", "no schtasks"])
def test_no_helper_means_no_windows_buttons_and_a_plain_refusal(tmp_path: Path, how: str) -> None:
    changes = {"admin_helper_root": None} if how == "not configured" else {}
    with _desk(tmp_path, **changes) as desk:
        if how == "no request folder":
            (desk.host.root / "request").rmdir()
        if how == "no schtasks":
            (desk.host.bin / "schtasks.exe").unlink()
        text = page(desk.c)
        assert "The Windows admin helper is not set up yet; see the sitting steps." in text
        assert "docs/anywhere-sitting.md, step 2" in text
        assert _offered(text) == set(LINUX)  # nothing risky is offered
        for key in WINDOWS:
            n = {"n": str(ACTIONS[key].default)} if ACTIONS[key].choice else {}
            r = post(desk.c, do=key, **n)
            assert r.status_code == 409, (key, r.text)
            assert r.json()["message"].startswith("The Windows admin helper is not set up yet")
        assert desk.host.calls == []
        assert not (desk.host.root / "request" / "request.txt").exists()


def test_an_unproven_helper_is_offered_only_the_check_until_it_answers(
    desk: SimpleNamespace,
) -> None:
    (desk.host.root / "audit.log").unlink()  # installed, but it never ran
    text = page(desk.c)
    assert "It has not answered the desk yet" in text
    assert _offered(text) == {*LINUX, "helper-check"}
    r = post(desk.c, do="restart-wsl")
    assert r.status_code == 409 and "Tap Check the admin helper first" in r.json()["message"]
    assert tap(desk.c, "helper-check")[-1].json()["message"] == "The admin helper did it: pong."
    assert _offered(page(desk.c)) == set(ACTIONS)


def test_a_silent_helper_times_out_withdraws_its_request_and_says_set_it_up(
    desk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    short(monkeypatch, "helper-check", 0.6)
    desk.host.helper = "silent"
    start = time.monotonic()
    last = tap(desk.c, "helper-check")[-1]
    assert time.monotonic() - start < 5
    assert last.status_code == 504
    message = last.json()["message"]
    assert message.startswith("The Windows admin helper did not answer within 0 s.")
    assert "The Windows admin helper is not set up yet; see the sitting steps." in message
    assert "The request was withdrawn, so it cannot run later." in message
    assert not (desk.host.root / "request" / "request.txt").exists()
    # the log has it; the page now offers only the check again
    end = logged(desk.log)[-1]
    assert end["result"] == "timed out" and end["silent"] is True
    assert _offered(page(desk.c)) == {*LINUX, "helper-check"}


@pytest.mark.parametrize(
    ("mode", "words"),
    [
        ("denied", "Windows did not let the desk start the admin helper (access denied)."),
        ("no-task", "Windows has no admin helper task."),
    ],
)
def test_a_helper_task_windows_will_not_start_is_refused_and_withdrawn(
    desk: SimpleNamespace, mode: str, words: str
) -> None:
    desk.host.helper = mode
    last = tap(desk.c, "restart-tailscale")[-1]
    assert last.status_code == 409 and words in last.json()["message"]
    assert not (desk.host.root / "request" / "request.txt").exists()
    assert desk.host.requests == []
    assert logged(desk.log)[-1]["result"] == "refused"


def test_the_helper_refusing_or_failing_is_said_in_plain_words(desk: SimpleNamespace) -> None:
    desk.host.helper = "reject"  # e.g. an older helper without the verb
    r = tap(desk.c, "pause-updates", 7)[-1]
    assert r.status_code == 409
    assert r.json()["message"] == "The admin helper refused the request: unknown verb. Nothing ran."
    desk.host.helper = "fail"
    r = tap(desk.c, "restart-wsl")[-1]
    assert r.status_code == 502
    assert r.json()["message"] == "The admin helper tried and failed: no WSL service found."


def test_a_helper_that_started_but_did_not_finish_times_out_without_blaming_the_setup(
    desk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    short(monkeypatch, "tailscale-up", 0.6)
    desk.host.helper = "started"
    r = tap(desk.c, "tailscale-up")[-1]
    assert r.status_code == 504
    assert "started but did not finish" in r.json()["message"]
    assert "not set up" not in r.json()["message"]
    assert _offered(page(desk.c)) == set(ACTIONS)  # it did answer, so it stays offered


# ---- time-outs ------------------------------------------------------------------------------


def test_a_linux_action_past_its_time_out_is_stopped_and_said(
    desk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    short(monkeypatch, "restart-clipdesk", 1.0)
    desk.host.hang = {"restart"}
    r = tap(desk.c, "restart-clipdesk")[-1]
    assert r.status_code == 504
    assert r.json()["message"] == "It did not finish within 1 s and was stopped."
    short(monkeypatch, "fleet-up", 1.0)
    desk.host.hang = {"start"}
    r = tap(desk.c, "fleet-up")[-1]
    assert r.status_code == 504 and "fleet-up carries on by itself" in r.json()["message"]
    assert [x["result"] for x in logged(desk.log) if x["result"] != "running"] == [
        "timed out",
        "timed out",
    ]


async def test_a_command_past_its_time_out_is_killed_with_its_children(tmp_path: Path) -> None:
    script = tmp_path / "hang.sh"
    script.write_text(f"#!/bin/sh\necho $$ > '{tmp_path}/pid'\n(sleep 30) &\nsleep 30\n")
    script.chmod(0o700)
    start = time.monotonic()
    done = await runqueue.run_argv([str(script)], 0.5)
    assert done.timed_out and time.monotonic() - start < 4
    pgid = int((tmp_path / "pid").read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("the timed-out command's process group is still alive")


# ---- rate limit and one at a time ---------------------------------------------------------


async def test_the_same_action_waits_a_minute_and_all_take_ten_per_ten_minutes(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path)
    now = [1_000_000.0]
    rq = RunQueue(_settings(tmp_path, host), run=host, clock=lambda: now[0])
    clip = ACTIONS["restart-clipdesk"]
    assert (await rq.run(clip, None, _rid(1), OWNER)).result == "ok"
    now[0] += 30
    with pytest.raises(ActionError) as again:
        await rq.run(clip, None, _rid(2), OWNER)
    assert again.value.status == 429 and "ran 30 s ago. Try again in 31 s." in str(again.value)
    now[0] += 31
    n = 1
    for key in LINUX:  # five more: six in all
        n += 1
        assert (await rq.run(ACTIONS[key], None, _rid(n), OWNER)).result == "ok", key
    now[0] += 61
    for key in ("restart-clipdesk", "restart-telegram", "fleet-up", "serve-maps"):  # ten
        n += 1
        assert (await rq.run(ACTIONS[key], None, _rid(n), OWNER)).result == "ok", key
    with pytest.raises(ActionError) as limit:
        await rq.run(ACTIONS["restart-desk"], None, _rid(99), OWNER)
    assert limit.value.status == 429
    assert "10 runs in the last 10 minutes is the limit." in str(limit.value)
    now[0] = 1_000_000.0 + 601  # the first run is out of the window
    assert (await rq.run(ACTIONS["restart-desk"], None, _rid(100), OWNER)).result == "ok"
    # refusals are not runs: the log has only the eleven that ran
    assert len([r for r in rq.log.runs() if r.result == "ok"]) == 11
    assert host.bad == []


async def test_one_windows_action_at_a_time_and_linux_is_not_held_up(tmp_path: Path) -> None:
    host = Host(tmp_path)
    host.helper_slow = 0.5
    rq = RunQueue(_settings(tmp_path, host), run=host)
    first = asyncio.create_task(rq.run(ACTIONS["helper-check"], None, _rid(1), OWNER))
    await asyncio.sleep(0.1)
    with pytest.raises(ActionError) as busy:
        await rq.run(ACTIONS["pause-updates"], 7, _rid(2), OWNER)
    assert busy.value.status == 409
    assert str(busy.value) == "A Windows action is already running. Wait for it to finish."
    assert (await rq.run(ACTIONS["restart-clipdesk"], None, _rid(3), OWNER)).result == "ok"
    assert (await first).result == "ok"
    assert (await rq.run(ACTIONS["pause-updates"], 7, _rid(4), OWNER)).result == "ok"
    assert host.bad == []


async def test_two_lanes_at_once_with_nine_runs_in_the_window_start_only_one(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path)
    now = [1_000_000.0]
    rq = RunQueue(_settings(tmp_path, host), run=host, clock=lambda: now[0])
    for i in range(9):
        done = runqueue.Run(_rid(i), "restart-telegram", OWNER, now[0] - 300, result="ok")
        assert rq.log.append(runqueue._record(done))
    both = await asyncio.gather(
        rq.run(ACTIONS["helper-check"], None, _rid(20), OWNER),
        rq.run(ACTIONS["restart-clipdesk"], None, _rid(21), OWNER),
        return_exceptions=True,
    )
    started = [r for r in both if not isinstance(r, ActionError)]
    refused = [r for r in both if isinstance(r, ActionError)]
    assert len(started) == 1 and started[0].result == "ok"
    assert len(refused) == 1 and refused[0].status == 429
    assert len(rq.log.runs()) == 10
    assert host.bad == []


async def test_an_unwritable_run_log_refuses_the_run_and_nothing_runs(tmp_path: Path) -> None:
    host = Host(tmp_path)
    rq = RunQueue(_settings(tmp_path, host), run=host)
    rq.log.path.mkdir(parents=True)  # a directory where the log file should be
    for key in ("restart-clipdesk", "helper-check"):
        with pytest.raises(ActionError) as refused:
            await rq.run(ACTIONS[key], None, _rid(1), OWNER)
        assert refused.value.status == 503
        assert str(refused.value) == "The run log cannot be written, so nothing ran."
    assert host.calls == [] and host.requests == []
    assert not (host.root / "request" / "request.txt").exists()


# ---- the audit ------------------------------------------------------------------------------


def test_each_run_is_logged_with_who_what_when_result_and_duration_and_no_secrets(
    desk: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="hive.gateway.audit")
    desk.host.fail_with = (
        f"Job for clip-desk.service failed: {SECRET} see https://login.example/a/PLANTED "
        "by someone@example.test\n"
    )
    before = time.time()
    r = tap(desk.c, "restart-clipdesk")[-1]
    assert r.status_code == 502 and r.json()["message"].startswith("It failed: Job for")
    assert "PLANTED" not in r.text and "someone@example.test" not in r.text
    start, end = logged(desk.log)
    assert set(start) == {"id", "who", "action", "start", "result"}
    assert start["who"] == OWNER and start["action"] == "restart-clipdesk"
    assert start["result"] == "running" and before <= start["start"] <= time.time()
    assert set(end) == {"id", "who", "action", "start", "result", "duration_s", "detail"}
    assert end["id"] == start["id"] and end["start"] == start["start"]
    assert end["result"] == "failed" and 0 <= end["duration_s"] < 5
    text = desk.log.read_text()
    assert "PLANTED" not in text and "https://" not in text and "someone@" not in text
    assert stat.S_IMODE(desk.log.stat().st_mode) == 0o600
    [journal] = [
        m.getMessage()
        for m in caplog.records
        if "action=run subject=restart-clipdesk outcome=failed" in m.getMessage()
    ]
    assert "who=owner" in journal and "start=" in journal and "duration=" in journal
    assert OWNER not in journal and "PLANTED" not in journal


def test_the_page_lists_the_last_twenty_runs_newest_first(desk: SimpleNamespace) -> None:
    now = time.time()
    desk.log.parent.mkdir(parents=True, exist_ok=True)
    with open(desk.log, "w") as fh:
        for i in range(25):
            rec = {
                "id": _rid(i),
                "who": OWNER,
                "action": "fleet-up",
                "start": now - 5000 + i * 100,
                "result": "ok" if i % 2 else "failed",
                "duration_s": 12.5,
                "detail": f"run number {i:02d}",
            }
            fh.write(json.dumps(rec) + "\n")
    text = page(desk.c)
    shown = re.findall(r"run number (\d\d)", text)
    assert shown == [f"{i:02d}" for i in range(24, 4, -1)]
    assert text.count("class='mrow mrow--") == 20
    assert "by you" in text and OWNER not in text
    assert "took 12\u00a0s" in text  # the unit never wraps alone


# ---- settling, the desk guard and results ----------------------------------------------------


async def test_a_run_the_desk_did_not_see_finish_is_settled_from_the_logs(tmp_path: Path) -> None:
    host = Host(tmp_path)
    settings = _settings(tmp_path, host)
    boot = 2_000_000.0
    lines = [
        {
            "id": _rid(1),
            "who": OWNER,
            "action": "restart-desk",
            "start": boot - 6,
            "result": "running",
        },
        {
            "id": _rid(2),
            "who": OWNER,
            "action": "restart-wsl",
            "start": boot - 90,
            "result": "running",
            "nonce": "hive-aaaaaaaaaaaaaaaa",
        },
        {
            "id": _rid(3),
            "who": OWNER,
            "action": "restart-tailscale",
            "start": boot - 30,
            "result": "running",
            "nonce": "hive-bbbbbbbbbbbbbbbb",
        },
        {
            "id": _rid(4),
            "who": OWNER,
            "action": "fleet-up",
            "start": boot - 10,
            "result": "running",
        },
    ]
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / runqueue.LOG_NAME).write_text("".join(json.dumps(x) + "\n" for x in lines))
    host.audit("hive-aaaaaaaaaaaaaaaa", "restart-wsl", "-", "started", "")
    host.audit("hive-aaaaaaaaaaaaaaaa", "restart-wsl", "-", "ok", "restarted service WSLService")
    req = host.root / "request" / "request.txt"
    req.write_bytes(b"hive-bbbbbbbbbbbbbbbb restart-tailscale\n")  # never taken
    rq = RunQueue(settings, run=host, clock=lambda: boot)
    runs = {r.action: r for r in await rq.settled()}
    assert runs["restart-desk"].result == "ok" and runs["restart-desk"].duration_s == 6
    assert runs["restart-wsl"].result == "ok"
    assert "The desk restarted during the run." in runs["restart-wsl"].detail
    assert runs["restart-tailscale"].result == "unknown"
    assert "The request was withdrawn." in runs["restart-tailscale"].detail
    assert not req.exists()  # it can no longer fire later
    assert runs["fleet-up"].result == "unknown"
    # settled once, written once
    size = (settings.data_dir / runqueue.LOG_NAME).stat().st_size
    await rq.settled()
    assert (settings.data_dir / runqueue.LOG_NAME).stat().st_size == size
    assert host.calls == []


def test_the_desk_restarts_itself_only_when_it_is_the_service(desk: SimpleNamespace) -> None:
    desk.host.mainpid = 1  # some other process is the service
    r = tap(desk.c, "restart-desk")[-1]
    assert r.status_code == 409 and "would start a second desk" in r.json()["message"]
    assert all("restart" not in call for call in desk.host.calls)
    assert not desk.log.exists()  # refused before it started: not a run
    desk.host.mainpid = os.getpid()
    r = tap(desk.c, "restart-desk")[-1]
    assert r.status_code == 200 and r.json()["reload"] == 8
    assert r.json()["message"].startswith("The desk is restarting.")


def test_a_bot_that_stays_down_after_its_restart_is_a_failure(desk: SimpleNamespace) -> None:
    desk.host.active = "inactive"
    r = tap(desk.c, "restart-telegram")[-1]
    assert r.status_code == 502
    assert r.json()["message"] == (
        "The Telegram bot is not running after the restart (inactive). It stays off until "
        "it is set up (docs/telegram.md)."
    )


def test_serve_maps_tailscale_would_not_take_are_named(desk: SimpleNamespace) -> None:
    desk.host.serve = "serve: failed for :8446\nserve: :8443 ok\nserve: failed for :8448\n"
    r = tap(desk.c, "serve-maps")[-1]
    assert r.status_code == 502
    assert r.json()["message"].startswith("Tailscale did not take ports 8446, 8448.")


def test_a_long_run_answers_pending_then_its_result(desk: SimpleNamespace) -> None:
    desk.host.slow = 1.5
    replies = tap(desk.c, "fleet-up")
    codes = [r.status_code for r in replies]
    assert codes[1] == 202 and codes[-1] == 200, codes
    assert replies[1].json() == {"ok": True, "message": RUN_PENDING, "pending": True}
    assert replies[-1].json()["message"].startswith("Fleet-up ran to the end")
    assert len(desk.host.calls) == 1  # asked again, not run again


# ---- the page -------------------------------------------------------------------------------


def test_the_page_shows_each_button_with_its_exact_command_and_fixed_choices(
    desk: SimpleNamespace,
) -> None:
    text = page(desk.c)
    assert "<a href='/run' aria-current=page>Run</a>" in text
    for a in ACTIONS.values():
        assert html.escape(a.label, quote=False) in text, a.key
        assert html.escape(a.command(), quote=False) in text, a.key
    assert _offered(text) == set(ACTIONS)
    assert text.count("action='/act/run'") == len(ACTIONS)
    assert text.count("<select ") == 1  # the days to pause updates; no restart hour
    days = re.search(r"<select id=rq-pause-updates name=n>(.*?)</select>", text)[1]
    assert re.findall(r"<option value=(\d+)", days) == ["1", "3", "7", "14", "21", "35"]
    assert "<option value=7 selected>7 days</option>" in days
    assert "Cancel a scheduled restart" in text and "run shutdown /a" in text
    assert "Restart the PC at a set hour" in text and "must be cancellable" in text
    assert "No runs yet." in text
    assert " data-run" in text  # the page refreshes itself on the run event


def test_a_run_in_progress_shows_as_running(desk: SimpleNamespace) -> None:
    desk.host.slow = 1.2
    first = post(desk.c, do="fleet-up").json()
    r = post(desk.c, do="fleet-up", rid=first["rid"], step=first["step"])
    assert r.status_code == 202
    assert "<span class='mstate mstate--run'>Running</span>" in page(desk.c)
    time.sleep(1.4)
    assert "<span class='mstate mstate--ok'>OK</span>" in page(desk.c)


# The page's own timers run fast here: the 9 s a result stays on its card, and the 2 s check
# for a refresh that had to wait.
FAST_TIMERS = (
    "var _st=window.setTimeout,_si=window.setInterval;"
    "window.setTimeout=function(f,ms){return _st(f,ms===9000?200:ms);};"
    "window.setInterval=function(f,ms){return _si(f,ms===2000?50:ms);};"
)

RESULT_THEN_REFRESH_DRIVER = """
var sent=[];
window.fetch=function(u,o){
if(o&&o.method==='POST'){var b=String(o.body);sent.push(b);
var j=b.indexOf('step=')>=0?{ok:true,pending:false,message:'The admin helper did it: paused.'}:
{ok:true,confirm:true,step:'s1',rid:'web-00000000000000aa',extra:{do:'pause-updates',n:'14'},
label:'Tap again to run',detail:'Pauses Windows Update.'};
return Promise.resolve({ok:true,json:function(){return Promise.resolve(j);}});}
return Promise.resolve({ok:true,text:function(){return Promise.resolve(window.__next);}});};
window.__next=document.documentElement.outerHTML.replace('No runs yet.','Paused for 14 days.');
window.__es.onopen();
function wait(ms){return new Promise(function(r){setTimeout(r,ms);});}
var fm=q('input[name=do][value=pause-updates]').form,s=fm.querySelector('select');
s.value='14';s.dispatchEvent(new Event('change',{bubbles:true}));
fm.querySelector('button').click();await wait(60);
R.armed=fm.getAttribute('data-step');
fm.querySelector('button').click();await wait(60);
R.sent=sent;R.shown=fm.classList.contains('is-shown');
R.said=fm.querySelector('.act-ok').textContent;
window.__es.l.run();await wait(60);
R.during=q('main').textContent.indexOf('Paused for 14 days.')>=0;
await wait(600);
R.after=q('main').textContent.indexOf('Paused for 14 days.')>=0;
"""


@needs_chrome
def test_the_list_refreshes_after_a_run_from_a_card_with_a_changed_choice(
    tmp_path: Path, desk: SimpleNamespace
) -> None:
    """A run's result stays on its card a few seconds, and the run event's refresh waits for
    it. Then the card is back to its defaults and the waiting refresh shows the new run, even
    though the owner picked a choice (14 days) that is not the default."""
    r = _in_chrome(
        tmp_path,
        page(desk.c),
        RESULT_THEN_REFRESH_DRIVER,
        (820, 1180),
        prelude=FAKE_LIVE + FAST_TIMERS,
    )
    assert r["armed"] == "s1"
    first, second = r["sent"]
    assert "do=pause-updates" in first and "n=14" in first and "step=" not in first
    assert "step=s1" in second and "n=14" in second
    assert r["shown"] and r["said"] == "The admin helper did it: paused."
    assert not r["during"]  # the result on the card is not swapped away under the owner
    assert r["after"]  # then the list shows the run


def test_the_docs_table_has_one_row_per_button_with_its_time_out() -> None:
    doc = (Path(__file__).parents[2] / "docs" / "run-queue.md").read_text()
    rows = {
        m[0]: m[1]
        for m in re.findall(r"^\| ([^|]+?) \| [^|]+ \| [^|]*?\((\d+ (?:s|min))\) \|", doc, re.M)
    }
    assert set(rows) == {a.label for a in ACTIONS.values()}
    for a in ACTIONS.values():
        want = f"{int(a.timeout_s // 60)} min" if a.timeout_s >= 120 else f"{int(a.timeout_s)} s"
        assert rows[a.label] == want, a.key
    assert "Cancel a scheduled restart" in doc and "shutdown /a" in doc
    assert "Restart the PC at a set hour" in doc and "follow-up" in doc
