"""Review pages: live push when a page closes, Close / Close all old, and the nudge.

Lavish is never touched: the state file is a fixture and ``lavish-axi`` is a fake script
that records its argv and marks the session ended, as the real one does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import stat
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from hive.gateway import reviews
from hive.gateway.actions import Tokens
from hive.gateway.app import create_app
from hive.gateway.live import LiveHub
from hive.gateway.reviews import Review, read_reviews
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import SnapshotProvider

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
GOOD = {
    "tailscale-user-login": OWNER,
    "host": HOST,
    "origin": f"https://{HOST}",
    "content-type": "application/x-www-form-urlencoded",
}
CSRF = Tokens(b"k" * 32)
RID = "web-0123456789abcdef"
NOW = time.time()
DAY = 86400


def _iso(age_s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - age_s))


FAKE_LAVISH = """#!/usr/bin/env python3
import json, sys
state, log = sys.argv[0] + ".state", sys.argv[0] + ".log"
open(log, "a").write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:2] == ["end"]:
    path = open(state).read().strip()
    d = json.load(open(path))
    for s in d["sessions"].values():
        if s["file"] == sys.argv[2]:
            s["status"] = "ended"
    json.dump(d, open(path, "w"))
"""


class Lavish:
    """A fake Lavish: a state file plus a ``lavish-axi`` that ends sessions in it."""

    def __init__(self, root: Path) -> None:
        self.state = root / "lavish-state.json"
        self.binary = root / "lavish-axi"
        self.binary.write_text(FAKE_LAVISH)
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        Path(f"{self.binary}.state").write_text(str(self.state))
        self.write({})

    def write(self, ages: dict[str, float], status: dict[str, str] | None = None) -> None:
        sessions = {
            k: {
                "key": k,
                "file": f"/w/{k}.html",
                "status": (status or {}).get(k, "open"),
                "updated_at": _iso(age),
                "chat": [],
            }
            for k, age in ages.items()
        }
        self.state.write_text(json.dumps({"sessions": sessions}))

    def ended(self) -> list[str]:
        return [
            k
            for k, s in json.loads(self.state.read_text())["sessions"].items()
            if s["status"] != "open"
        ]

    def calls(self) -> list[str]:
        log = Path(f"{self.binary}.log")
        return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def lavish(tmp_path: Path) -> Lavish:
    return Lavish(tmp_path)


def _settings(tmp_path: Path, lavish: Lavish, **kw) -> GatewaySettings:
    (tmp_path / "bin").mkdir(exist_ok=True)
    snap = tmp_path / "bin" / "fm-fleet-snapshot.sh"
    snap.write_text(f"#!/usr/bin/env bash\ncat '{FIXTURE}'\n")
    snap.chmod(snap.stat().st_mode | stat.S_IXUSR)
    return GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=tmp_path,
        snapshot_ttl_s=0,
        board_url="https://board.example.ts.net:8445",
        lavish_state=lavish.state,
        lavish_axi=lavish.binary,
        data_dir=tmp_path / "data",
        **kw,
    )


@pytest.fixture
def client(tmp_path: Path, lavish: Lavish) -> TestClient:
    app = create_app(_settings(tmp_path, lavish), tokens=CSRF)
    return TestClient(app, client=("127.0.0.1", 5000), follow_redirects=False)


def post(client: TestClient, name: str, headers: dict | None = None, **fields: str):
    data = {"csrf": CSRF.csrf(), "next": "/", "rid": RID, **fields}
    return client.post(f"/act/{name}", content=urlencode(data), headers=headers or GOOD)


def jpost(client: TestClient, name: str, **fields: str):
    return post(client, name, headers={**GOOD, "accept": "application/json"}, **fields)


# ---- nudge thresholds -------------------------------------------------------------


def _stale(lavish: Lavish, ages: dict[str, float]) -> list[str]:
    lavish.write(ages)
    return sorted(r.key for r in read_reviews(lavish.state, set(), NOW) if r.stale)


def test_a_page_older_than_two_days_looks_done(lavish: Lavish) -> None:
    assert _stale(lavish, {"fresh": 3600, "edge": 2 * DAY - 60, "old": 2 * DAY + 60}) == ["old"]


def test_eight_open_pages_are_fine_nine_flags_the_oldest(lavish: Lavish) -> None:
    eight = {f"p{i}": 60 * (i + 1) for i in range(8)}
    assert _stale(lavish, eight) == []
    nine = {**eight, "p8": 60 * 9}
    assert _stale(lavish, nine) == ["p8"]  # the oldest one beyond the newest eight


def test_old_and_overflow_combine_without_double_counting(lavish: Lavish) -> None:
    ages = {f"p{i}": 60 * (i + 1) for i in range(10)}
    ages["old1"] = 3 * DAY
    # 11 open: 3 beyond eight. The old one counts toward them; two more recent ones join.
    assert _stale(lavish, ages) == ["old1", "p8", "p9"]


def test_a_page_with_a_waiting_reply_never_looks_done(lavish: Lavish) -> None:
    lavish.write({"a": 5 * DAY, "b": 5 * DAY})
    d = json.loads(lavish.state.read_text())
    d["sessions"]["a"]["chat"] = [{"role": "agent"}]
    lavish.state.write_text(json.dumps(d))
    flags = {r.key: r.stale for r in read_reviews(lavish.state, set(), NOW)}
    assert flags == {"a": False, "b": True}


def test_the_desk_shows_the_nudge_and_tags_the_pages(client: TestClient, lavish: Lavish) -> None:
    lavish.write({"a": 60, "b": 3 * DAY, "c": 4 * DAY})
    html = client.get("/", headers=GOOD).text
    assert "2 pages look done: close them?" in html
    assert html.count("<span class=rv__done>looks done</span>") == 2 * 2  # list + popup sheet
    assert ">Close 2</button>" in html and ">Close all old</button>" in html
    lavish.write({"a": 60, "b": 3600})
    html = client.get("/", headers=GOOD).text
    assert "look done" not in html and "Close all old" not in html
    assert html.count(">Close</button>") == 2  # every row still has its own Close


def test_no_close_controls_without_lavish_axi(tmp_path: Path, lavish: Lavish) -> None:
    lavish.write({"a": 60, "b": 3 * DAY})
    settings = replace(_settings(tmp_path, lavish), lavish_axi=None)
    html = TestClient(create_app(settings), client=("127.0.0.1", 5000)).get("/", headers=GOOD).text
    section = html.split("<section class=rvs>", 1)[1].split("</section>", 1)[0]
    assert "Review pages · 2" in section and "Close" not in section and "look done" not in section


# ---- Close and Close all old ------------------------------------------------------


def test_close_is_post_only_and_owner_only(client: TestClient, lavish: Lavish) -> None:
    lavish.write({"a": 60})
    assert client.get("/act/review-close", headers=GOOD).status_code == 405
    no_login = {k: v for k, v in GOOD.items() if k != "tailscale-user-login"}
    assert post(client, "review-close", no_login, key="a").status_code == 403
    other = {**GOOD, "tailscale-user-login": "intruder@example.test"}
    assert post(client, "review-close", other, key="a").status_code == 403
    assert post(client, "review-close", key="a", csrf="wrong").status_code == 403
    cross = {**GOOD, "origin": "https://evil.example"}
    assert post(client, "review-close", cross, key="a").status_code == 403
    assert lavish.calls() == [] and lavish.ended() == []


def test_close_needs_the_confirm_step_before_anything_runs(
    client: TestClient, lavish: Lavish
) -> None:
    lavish.write({"a": 60, "b": 60})
    res = post(client, "review-close", key="a")  # no JS: a confirm page
    assert res.status_code == 200 and "Confirm" in res.text
    assert lavish.calls() == [] and lavish.ended() == []
    step = re.search(r'name=step value="([^"]+)"', res.text).group(1)
    # a step for another page does not open this one
    wrong = jpost(client, "review-close", key="b", step=step, rid=RID)
    assert wrong.json().get("confirm") is True and lavish.calls() == []
    ok = post(client, "review-close", key="a", step=step, rid=RID)
    assert ok.status_code == 200 and "Closed 1 review page." in ok.text
    assert lavish.calls() == ["end /w/a.html"] and lavish.ended() == ["a"]


def test_close_json_flow_and_audit_line(
    client: TestClient, lavish: Lavish, caplog: pytest.LogCaptureFixture
) -> None:
    lavish.write({"a": 60})
    caplog.set_level(logging.INFO, logger="hive.gateway.audit")
    first = jpost(client, "review-close", key="a").json()
    assert first["ok"] and first["confirm"] and first["label"] == "Tap again to close"
    done = jpost(client, "review-close", key="a", step=first["step"], rid=first["rid"]).json()
    assert done == {"ok": True, "message": "Closed 1 review page."}
    assert any(
        "action=review-close subject=a outcome=done" in r.getMessage() for r in caplog.records
    )
    # the same request id again does not end anything twice
    jpost(client, "review-close", key="a", step=first["step"], rid=first["rid"])
    assert lavish.calls() == ["end /w/a.html"]


def test_close_of_an_already_closed_page_is_a_quiet_success(
    client: TestClient, lavish: Lavish
) -> None:
    lavish.write({"a": 60}, {"a": "ended"})
    res = jpost(client, "review-close", key="a").json()
    assert res == {"ok": True, "message": "That review page is already closed."}
    assert lavish.calls() == []


def test_close_takes_the_file_from_lavish_state_not_the_request(
    client: TestClient, lavish: Lavish
) -> None:
    lavish.write({"a": 60})
    first = jpost(client, "review-close", key="a", file="/etc/passwd").json()
    jpost(client, "review-close", key="a", file="/etc/passwd", step=first["step"], rid=first["rid"])
    assert lavish.calls() == ["end /w/a.html"]
    assert jpost(client, "review-close", key="../x").status_code == 400


def test_close_all_old_ends_exactly_the_confirmed_old_pages(
    client: TestClient, lavish: Lavish
) -> None:
    lavish.write({"new": 60, "o1": 3 * DAY, "o2": 5 * DAY})
    first = jpost(client, "review-close-old").json()
    assert first["confirm"] and first["label"] == "Tap again: close 2"
    assert lavish.calls() == []
    keys = first["extra"]["keys"]
    assert sorted(keys.split(",")) == ["o1", "o2"]
    # a step is bound to its list: a different list is refused a run
    bad = jpost(client, "review-close-old", keys="new", step=first["step"], rid=first["rid"])
    assert bad.json().get("confirm") is True and lavish.calls() == []
    done = jpost(client, "review-close-old", keys=keys, step=first["step"], rid=first["rid"])
    assert done.json() == {"ok": True, "message": "Closed 2 review pages."}
    assert sorted(lavish.ended()) == ["o1", "o2"]


def test_close_all_old_with_nothing_old_does_nothing(client: TestClient, lavish: Lavish) -> None:
    lavish.write({"a": 60})
    res = jpost(client, "review-close-old").json()
    assert res == {"ok": True, "message": "No review pages look done."}
    assert lavish.calls() == []


def test_a_failing_lavish_is_reported_not_hidden(tmp_path: Path, lavish: Lavish) -> None:
    lavish.write({"a": 60})
    lavish.binary.write_text("#!/usr/bin/env bash\nexit 3\n")
    app = create_app(_settings(tmp_path, lavish), tokens=CSRF)
    c = TestClient(app, client=("127.0.0.1", 5000))
    first = jpost(c, "review-close", key="a").json()
    res = jpost(c, "review-close", key="a", step=first["step"], rid=first["rid"])
    assert res.status_code == 502 and "could not be closed" in res.json()["message"]


# ---- live push --------------------------------------------------------------------


def _hub(tmp_path: Path, lavish: Lavish) -> LiveHub:
    s = _settings(tmp_path, lavish)
    return LiveHub(s, SnapshotProvider(s))


def test_closing_a_page_anywhere_pushes_a_desk_event(tmp_path: Path, lavish: Lavish) -> None:
    lavish.write({"a": 60, "b": 60})
    hub = _hub(tmp_path, lavish)
    q = hub.subscribe()

    async def scenario() -> list[str]:
        await hub._look_at_reviews()  # first look: a baseline, nothing is published
        assert q.empty()
        await hub._look_at_reviews()  # unchanged file: nothing
        assert q.empty()
        lavish.write({"a": 60, "b": 60}, {"b": "ended"})  # closed in Lavish itself
        await hub._look_at_reviews()
        out = []
        while not q.empty():
            out.append(q.get_nowait())
        return out

    msgs = asyncio.run(scenario())
    assert len(msgs) == 1 and msgs[0].startswith("event: desk\n")


def test_a_state_rewrite_that_changes_nothing_shown_is_silent(
    tmp_path: Path, lavish: Lavish
) -> None:
    lavish.write({"a": 60})
    hub = _hub(tmp_path, lavish)
    q = hub.subscribe()

    async def scenario() -> bool:
        await hub._look_at_reviews()
        d = json.loads(lavish.state.read_text())
        d["sessions"]["a"]["prompts"] = ["noise"]  # file moves, the list does not
        lavish.state.write_text(json.dumps(d))
        await hub._look_at_reviews()
        return q.empty()

    assert asyncio.run(scenario())


def test_the_close_action_publishes_a_desk_event(tmp_path: Path, lavish: Lavish) -> None:
    """Closing from the desk reaches every open desk at once, without waiting for the watcher."""
    from hive.gateway.actions import RunOnce
    from hive.gateway.app import _review_action

    lavish.write({"a": 60})
    settings, runs, published = _settings(tmp_path, lavish), RunOnce(), []

    async def close(**form: str):
        return await _review_action(
            "review-close",
            {"key": "a", "rid": RID, **form},
            settings,
            CSRF,
            runs,
            "/",
            True,
            published.append,
        )

    async def scenario() -> None:
        step = CSRF.step_up("review", _subject("review-close", ["a"]))
        res = await close(step=step)
        assert res.status_code == 200 and published == ["desk"]

    asyncio.run(scenario())
    assert lavish.ended() == ["a"]


def _subject(name: str, keys: list[str]) -> str:
    from hive.gateway.app import _step_subject

    return _step_subject(name, keys)


def test_the_watcher_stats_the_state_file_before_reading_it(tmp_path: Path) -> None:
    assert reviews.state_signature(None) is None
    assert reviews.state_signature(tmp_path / "missing.json") is None
    f = tmp_path / "s.json"
    f.write_text("{}")
    before = reviews.state_signature(f)
    f.write_text('{"sessions": {}}')
    assert reviews.state_signature(f) != before


def test_review_dataclass_default_is_not_stale() -> None:
    assert Review("k", "t", "", False, "").stale is False


# ---- layout: the helper and "Updated" lines sit above the Describe-a-goal bar -----


def test_helper_and_updated_lines_come_before_the_bar(client: TestClient) -> None:
    html = client.get("/", headers=GOOD).text
    wrap = html.split("<div class=dbar-wrap>", 1)[1].split("</form></div>", 1)[0]
    note, stamp, bar = (
        wrap.index("class=dbar-note"),
        wrap.index("<p class=stamp>Updated"),
        wrap.index("<form method=post action='/act/delegate' class='dbar'"),
    )
    assert note < stamp < bar
    assert "Delegate to <b data-tname>first mate</b>" in wrap
    assert "tapping a project card retargets this bar" in wrap
    assert html.count("<p class=stamp>Updated") == 1  # not repeated below the bar
