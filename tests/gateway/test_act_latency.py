"""Every ``POST /act/*`` answers within a second, however slow firstmate is.

The fake firstmate home has a slow switch: once ``slow`` exists, the fleet snapshot, the inbox
(``fm-inbox.sh note`` waiting on the wake-queue lock), the hold intake, ``fm-control.sh`` and
``lavish-axi`` all sleep for seconds. The desk must not pass that on to the click. The real
work keeps going; the same request id asked again returns its result, or its failure.

The Machine page is held to the same second: its probes (PowerShell, Tailscale, systemctl)
hang on the same switch, and the page answers from what it has instead of waiting for them.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import stat
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from hive.gateway import actions
from hive.gateway.actions import Tokens
from hive.gateway.app import ACT_NAMES, _step_subject, create_app
from hive.gateway.settings import GatewaySettings
from tests.gateway.fmcfg import write_config

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
GOOD = {
    "tailscale-user-login": OWNER,
    "host": HOST,
    "origin": f"https://{HOST}",
    "content-type": "application/x-www-form-urlencoded",
    "accept": "application/json",
}
CSRF = Tokens(b"k" * 32)
BUDGET_S = 1.0
SLOW_S = 2

SLOW = '[ -e "$FM_HOME/slow" ] && sleep ' + str(SLOW_S)


def _write(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "bin").mkdir()
    b = tmp_path / "bin"
    _write(b / "fm-fleet-snapshot.sh", f"{SLOW}\ncat '{FIXTURE}'")
    _write(b / "fm-captain-hold.sh", f'{SLOW}\nIN="$(cat)"; echo "closed: ${{IN%%$\'\\t\'*}}"')
    _write(
        b / "fm-inbox.sh",
        f"{SLOW}\ncat >/dev/null\n"
        'echo \'{"schema":"fm-inbox-note.v1","outcome":"created","id":"n1"}\'',
    )
    _write(b / "fm-control.sh", f'{SLOW}\necho "ok: $2"')
    now = time.time()
    sessions = {
        k: {
            "key": k,
            "file": f"/w/{k}.html",
            "status": "open",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - age)),
            "chat": [],
        }
        for k, age in {"page-a": 60, "page-old": 5 * 86400}.items()
    }
    (tmp_path / "lavish-state.json").write_text(json.dumps({"sessions": sessions}))
    # lavish-axi is not run with FM_HOME, so its switch is an absolute path
    _write(tmp_path / "lavish-axi", f"[ -e '{tmp_path}/slow' ] && sleep {SLOW_S}\nexit 0")
    # the Machine page's probes: each launch is counted, then hangs while the switch is on
    _write(
        tmp_path / "probe",
        f"echo x >> '{tmp_path}/probed'\n[ -e '{tmp_path}/slow' ] && sleep {SLOW_S}\nexit 1",
    )
    return write_config(tmp_path)


@pytest.fixture
def client(home: Path) -> Iterator[TestClient]:
    settings = GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=home,
        snapshot_ttl_s=60,
        snapshot_timeout_s=6,
        lavish_state=home / "lavish-state.json",
        lavish_axi=home / "lavish-axi",
        data_dir=home / "data",
        live=False,
        powershell=home / "probe",
        tailscale=home / "probe",
        systemctl=home / "probe",
    )
    app = create_app(settings, tokens=CSRF)
    # One event loop for the whole test (as in production), so a run left going survives.
    with TestClient(app, client=("127.0.0.1", 5000), follow_redirects=False) as c:
        page = c.get("/", headers={k: v for k, v in GOOD.items() if k != "accept"})
        assert page.status_code == 200
        (home / "slow").write_text("")  # from here on everything firstmate runs is slow
        yield c
        # A run left going is still a child process (with its own children). The test is over,
        # so end them and let the loop reap them while it is open; a transport collected after
        # the loop is gone warns "Event loop is closed".
        _end_leftover_runs()


def _parents() -> dict[str, str]:
    """Every process's parent, by pid (zombies included: they are not reaped yet)."""
    parent = {}
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                parent[d.name] = (d / "stat").read_text().rsplit(")", 1)[1].split()[1]
            except (OSError, IndexError):
                continue  # gone already
    return parent


def _end_leftover_runs(timeout_s: float = 5) -> None:
    me = str(os.getpid())
    parent = _parents()
    mine, todo = [], [me]
    while todo:
        p = todo.pop()
        kids = [k for k, v in parent.items() if v == p]
        mine += kids
        todo += kids
    for pid in mine:
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid), signal.SIGKILL)
    deadline = time.monotonic() + timeout_s
    while me in _parents().values() and time.monotonic() < deadline:
        time.sleep(0.02)


def act(client: TestClient, name: str, **fields: str):
    data = {"csrf": CSRF.csrf(), "next": "/", **fields}
    return client.post(f"/act/{name}", content=urlencode(data), headers=GOOD)


def _rid(n: int) -> str:
    return f"web-{n:016x}"


# One executing request per action, as the page sends it after any confirm step.
CASES: dict[str, Callable[[str], dict]] = {
    "chat": lambda rid: {"rid": rid, "text": "status please"},
    "answer": lambda rid: {"rid": rid, "task": "beta-hold", "text": "Use warm", "release": "1"},
    "decision": lambda rid: {
        "rid": rid,
        "task": "beta-pr",
        "key": "board-review",
        "text": "Option A",
    },
    "delegate": lambda rid: {"rid": rid, "text": "Ship it", "project": "alpha"},
    "ticket": lambda rid: {
        "rid": rid,
        "mode": "edit",
        "project": "alpha",
        "ticket": "alpha-docs",
        "field": "title",
        "text": "Write the alpha docs, v2",
    },
    "merge": lambda rid: {
        "rid": rid,
        "task": "alpha-build",
        "step": CSRF.step_up("merge", "alpha-build"),
    },
    "control": lambda rid: {
        "rid": rid,
        "task": "alpha-build",
        "verb": "relaunch",
        "note": "pick up",
        "step": CSRF.step_up("control", f"alpha-build:relaunch:{rid}"),
    },
    # the fake systemctl ("probe") hangs while the switch is on, so the run is still going
    "run": lambda rid: {
        "rid": rid,
        "do": "restart-clipdesk",
        "step": CSRF.step_up("run", f"restart-clipdesk:-:{rid}"),
    },
    "review-close": lambda rid: {
        "rid": rid,
        "key": "page-a",
        "step": CSRF.step_up("review", _step_subject("review-close", ["page-a"])),
    },
    "review-close-old": lambda rid: {
        "rid": rid,
        "keys": "page-old",
        "step": CSRF.step_up("review", _step_subject("review-close-old", ["page-old"])),
    },
}


def test_every_act_route_has_a_timing_case() -> None:
    assert set(CASES) == set(ACT_NAMES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_act_answers_within_a_second_when_firstmate_is_slow(client: TestClient, name: str) -> None:
    start = time.monotonic()
    res = act(client, name, **CASES[name](_rid(1)))
    took = time.monotonic() - start
    assert took < BUDGET_S, f"/act/{name} took {took:.2f}s"
    assert res.status_code in (200, 202), res.text
    body = res.json()
    assert body["ok"] is True and "message" in body
    assert not body.get("confirm"), body  # the executing step, not a confirm
    print(f"TIMING {name} {took:.2f}s {res.status_code} {body['message']}")


@pytest.mark.parametrize("name", ["merge", "control"])
def test_the_confirm_step_answers_within_a_second_too(client: TestClient, name: str) -> None:
    fields = {"task": "alpha-build", "verb": "interrupt", "rid": _rid(2)}
    start = time.monotonic()
    res = act(client, name, **fields)
    took = time.monotonic() - start
    assert took < BUDGET_S, f"/act/{name} confirm took {took:.2f}s"
    body = res.json()
    assert body["ok"] is True and body["confirm"] is True and body["step"] and body["rid"]


@pytest.mark.parametrize("name", ["answer", "decision", "delegate", "merge", "control"])
def test_a_slow_run_is_pending_then_a_replay_is_success(client: TestClient, name: str) -> None:
    fields = CASES[name](_rid(3))
    first = act(client, name, **fields)
    assert first.status_code == 202 and first.json()["pending"] is True
    time.sleep(SLOW_S + 0.7)
    again = act(client, name, **fields)
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["ok"] is True and body["pending"] is False


def test_a_failure_after_accept_comes_back_on_the_same_request_id(
    client: TestClient, home: Path
) -> None:
    _write(
        home / "bin" / "fm-captain-hold.sh",
        f'cat >/dev/null; {SLOW}; echo "refused: task is not on hold"; exit 1',
    )
    fields = CASES["answer"](_rid(4))
    first = act(client, "answer", **fields)
    assert first.status_code == 202 and first.json()["ok"] is True
    time.sleep(SLOW_S + 0.7)
    late = act(client, "answer", **fields)
    assert late.status_code == 409
    assert late.json()["ok"] is False and "refused: task is not on hold" in late.json()["message"]


def test_a_late_inbox_failure_is_not_a_false_success(client: TestClient, home: Path) -> None:
    _write(home / "bin" / "fm-inbox.sh", f'cat >/dev/null; {SLOW}; echo "disk full"; exit 1')
    fields = CASES["decision"](_rid(5))
    first = act(client, "decision", **fields)
    assert first.status_code == 202
    time.sleep(SLOW_S + 0.7)
    late = act(client, "decision", **fields)
    assert late.status_code == 502 and late.json()["ok"] is False
    assert "disk full" in late.json()["message"]


def test_a_replay_after_the_card_is_gone_still_reads_as_success(
    client: TestClient, home: Path
) -> None:
    (home / "slow").unlink()
    fields = CASES["decision"](_rid(6))
    assert act(client, "decision", **fields).status_code == 200
    gone = act(client, "decision", **{**fields, "key": "no-longer-open"})  # same id, card gone
    assert gone.status_code == 200 and gone.json()["ok"] is True
    other = act(client, "decision", **{**fields, "rid": _rid(7), "key": "no-longer-open"})
    assert other.status_code == 409  # a different request is still refused


def test_the_machine_page_answers_within_a_second_when_every_probe_is_slow(
    client: TestClient, home: Path
) -> None:
    headers = {k: v for k, v in GOOD.items() if k != "accept"}
    for attempt in ("first", "second"):  # no reading yet, then a probe round still hanging
        start = time.monotonic()
        res = client.get("/machine", headers=headers)
        took = time.monotonic() - start
        assert took < BUDGET_S, f"GET /machine ({attempt}) took {took:.2f}s"
        assert res.status_code == 200
        # nothing has answered, so nothing is shown as fine
        assert res.text.count("<li class='mrow mrow--unknown'>") == 9
        assert "<li class='mrow mrow--ok'>" not in res.text
        print(f"TIMING machine {attempt} {took:.2f}s {res.status_code}")
    deadline = time.monotonic() + 2
    while not (home / "probed").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (home / "probed").exists()  # the probes did run, off the request


def test_the_wait_is_bounded_by_the_constant() -> None:
    assert 0 < actions.ACT_WAIT_S < BUDGET_S
