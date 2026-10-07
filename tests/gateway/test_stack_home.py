"""The Stack home (docs/design/T002-stack-home.html): lane hero, project cards, delegate
bar and quota chip, rendered from synthetic snapshots and a fake ``quota-axi``."""

from __future__ import annotations

import asyncio
import json
import re
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive.gateway.app import _safe_next, create_app
from hive.gateway.desk import build_desk, glance
from hive.gateway.pages import Ctx, _chip
from hive.gateway.quota import Quota, QuotaProvider, Window, parse_quota
from hive.gateway.settings import GatewaySettings

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
GOOD = {"tailscale-user-login": OWNER, "host": HOST}


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _client(tmp_path: Path, data: dict, quota: Path | None = None) -> TestClient:
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(data))
    _exe(tmp_path / "bin" / "fm-fleet-snapshot.sh", f"cat '{snap}'")
    settings = GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=tmp_path,
        snapshot_ttl_s=0,
        quota_axi=quota,
    )
    return TestClient(create_app(settings), client=("127.0.0.1", 5000))


def _rec(tid: str, repo: str, state: str, **kw: object) -> dict:
    return {"id": tid, "title": f"Title {tid}", "repo": repo, "state": state, **kw}


def _task(tid: str, project: str, state: str, **kw: object) -> dict:
    return {
        "id": tid,
        "kind": "ship",
        "project": f"/x/projects/{project}",
        "current_state": {"state": state, "detail": "busy"},
        **kw,
    }


def _snapshot(**kw: object) -> dict:
    return {"schema": "fm-fleet-snapshot.v1", "generated": "2030-01-02T03:04:05Z", **kw}


def _calm() -> dict:
    return _snapshot(
        backlog={
            "records": [
                _rec("a1", "alpha", "done"),
                _rec("a2", "alpha", "in_flight"),
                _rec("a3", "alpha", "queued"),
                _rec("b1", "beta", "done"),
                _rec("h1", "hive", "in_flight"),
            ]
        },
        tasks=[
            _task("a2", "alpha", "working"),
            _task("h1", "hive", "working"),
            # a second mate's own session: shown on its project, never counted as a loop
            _task("hive", "homes/hive", "working", kind="secondmate", secondmate_projects=["hive"]),
        ],
    )


def _cards(html: str) -> dict[str, str]:
    """project name -> card status, from the rendered cards."""
    pattern = r"<a class='pc pc--(\w+)[^']*' [^>]*data-name='([^']+)'"
    return {name: status for status, name in re.findall(pattern, html)}


# ---- glance: card state agrees with the needs-you lane ------------------------------


def test_every_project_with_a_needs_you_item_is_blocked_and_only_those(tmp_path: Path) -> None:
    desk = build_desk(json.loads(FIXTURE.read_text()))
    for p in desk.projects.values():
        assert (glance(p).status == "blocked") == bool(p.needs_you), p.name
    html = _client(tmp_path, json.loads(FIXTURE.read_text())).get("/", headers=GOOD).text
    assert _cards(html) == {"alpha": "blocked", "beta": "blocked", "General": "idle"}
    assert "<b>Waiting on you</b> — 2 items (open the project)" in html


def test_running_idle_activity_and_progress() -> None:
    desk = build_desk(_calm())
    alpha, beta, hive = desk.projects["alpha"], desk.projects["beta"], desk.projects["hive"]
    assert glance(alpha) == glance(alpha).__class__("running", "Working", "Title a2", 1, 3)
    assert glance(beta).status == "idle" and glance(beta).now == "Idle · nothing queued"
    assert (glance(beta).done, glance(beta).total) == (1, 1)
    assert hive.mate and not alpha.mate
    assert [c.id for c in hive.crews] == ["h1", "hive"]
    assert glance(hive).now == "Title h1"  # the mate session is not "2 crews in flight"
    assert desk.loops_running == 2


def test_idle_counts_parked_crews_but_not_the_mate_session() -> None:
    p = build_desk(_snapshot(tasks=[_task("w", "p", "parked")])).projects["p"]
    assert glance(p).now == "Idle · nothing queued · 1 crew parked"


def test_mate_record_without_repo_lands_on_the_mates_project() -> None:
    data = _snapshot(
        tasks=[
            _task("hive", "homes/hive", "parked", kind="secondmate", secondmate_projects=["hive"])
        ],
        secondmate_current={
            "records": [
                {
                    "id": "hive",
                    "decisions_open": [
                        {"id": "h9", "verb": "captain-hold", "summary": "Pick", "reason": "A?"}
                    ],
                }
            ]
        },
    )
    desk = build_desk(data)
    assert [(n.project, n.ref) for n in desk.needs_you] == [("hive", "h9")]
    assert glance(desk.projects["hive"]).status == "blocked"


# ---- the page --------------------------------------------------------------------


def _calm_empty() -> dict:
    data = _calm()
    data["backlog"]["records"] = [r for r in data["backlog"]["records"] if r["state"] != "queued"]
    return data


def test_calm_hero_counts_running_loops(tmp_path: Path) -> None:
    html = _client(tmp_path, _calm_empty()).get("/", headers=GOOD).text
    assert "class='nyl nyl--calm'" in html and "<span class=nyl__count>0</span>" in html
    assert "✓ all clear · 2 loops running" in html and "backlog is empty" in html
    assert _cards(html) == {"alpha": "running", "beta": "idle", "hive": "running"}
    assert "<span class=pc__mae>second mate</span>" in html
    assert "1 / 2 tasks" in html and ">done<span class=pc__open>" in html


def test_lane_is_the_backlog_grouped_by_project_without_worker_questions(tmp_path: Path) -> None:
    data = _calm()
    data["backlog"]["records"] += [
        _rec("a4", "alpha", "queued", blocked_by=["a3"], unresolved_blocker_ids=["a3"]),
        _rec("a5", "alpha", "queued", hold_reason="timed", captain_actionable=False),
        _rec("b2", "beta", "queued", hold_reason="Pick?", captain_actionable=True),
        *(_rec(f"a{n}", "alpha", "queued") for n in range(6, 10)),
    ]
    data["tasks"][0]["hints"] = {"open_decisions": [{"key": "ask", "summary": "Worker asks?"}]}
    html = _client(tmp_path, data).get("/", headers=GOOD).text
    lane = html.split("class=nyl__body>", 1)[1].split("</section></div>", 1)[0]
    assert re.findall(r"<span class=nyg__name>([^<]*)</span>", lane) == ["beta", "alpha"]
    assert "Worker asks?" not in html.split("Projects · tap")[0]
    assert "1 waiting on you" in lane and "<span class=nyl__count>8</span>" in html
    assert "+3 more" in lane  # alpha lists four queued, the rest is one tap away
    assert "held</span>" in lane and "blocked</span>" in lane
    assert "name=release value=1" in lane  # a held item is still answered as before


def test_registered_project_without_backlog_rows_still_has_an_idle_card(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "projects.md").write_text(
        "- rumble-arena [no-mistakes +yolo] - a game (added 2026-09-01)\n"
        "- plain-one - no mode (added 2026-09-02)\n"
    )
    mate = tmp_path / "mate"
    (mate / "data").mkdir(parents=True)
    (mate / "data" / "projects.md").write_text("- hive [no-mistakes] - the desk (added x)\n")
    data = _snapshot(
        backlog={"records": [_rec("a1", "alpha", "done")]},
        secondmate_current={"records": [{"id": "hive", "home": str(mate), "queued": []}]},
    )
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(data))
    _exe(home / "bin" / "fm-fleet-snapshot.sh", f"cat '{snap}'")
    settings = GatewaySettings(
        owner_login=OWNER, allowed_hosts=(HOST,), fm_home=home, snapshot_ttl_s=0
    )
    client = TestClient(create_app(settings), client=("127.0.0.1", 5000))
    html = client.get("/", headers=GOOD).text
    assert _cards(html) == {
        "alpha": "idle",
        "hive": "idle",
        "plain-one": "idle",
        "rumble-arena": "idle",
    }


def test_default_target_is_the_first_mate_with_several_projects(tmp_path: Path) -> None:
    html = _client(tmp_path, _calm()).get("/", headers=GOOD).text
    assert "is-selected' href" not in html
    assert "<span class=dbar__target data-target>→ first mate</span>" in html
    assert 'name=project value=""' in html and "<span data-nofocus> · no project focus" in html


def test_sole_project_is_selected_and_targeted_on_load(tmp_path: Path) -> None:
    data = _snapshot(backlog={"records": [_rec("a1", "alpha", "queued")]})
    html = _client(tmp_path, data).get("/", headers=GOOD).text
    assert "class='pc pc--idle is-selected' href='/p/alpha'" in html
    assert "→ first mate · alpha" in html and 'name=project value="alpha"' in html


def test_focus_selects_a_card_and_retargets_the_bar(tmp_path: Path) -> None:
    c = _client(tmp_path, _calm())
    html = c.get("/?focus=hive", headers=GOOD).text
    assert "class='pc pc--running is-selected' href='/p/hive'" in html
    assert "→ second mate · hive" in html and 'name=next value="/?focus=hive"' in html
    assert "<span data-nofocus hidden>" in html
    # unselected cards select on tap; an unknown focus falls back to the default
    assert "href='/?focus=alpha' data-card data-name='alpha'" in html
    assert "is-selected' href" not in c.get("/?focus=nope", headers=GOOD).text


def test_delegate_and_reply_inputs_are_text_entries(tmp_path: Path) -> None:
    html = _client(tmp_path, json.loads(FIXTURE.read_text())).get("/", headers=GOOD).text
    inputs = re.findall(r"<input [^>]*class=(dbar__in|nyi__reply)[^>]*>", html)
    assert "dbar__in" in inputs and "nyi__reply" in inputs
    for tag in re.findall(r"<input [^>]*class=(?:dbar__in|nyi__reply)[^>]*>", html):
        assert tag.startswith("<input type=text "), tag


def test_safe_next_allows_a_focused_home_only() -> None:
    assert _safe_next("/?focus=finance-app") == "/?focus=finance-app"
    assert _safe_next("/?focus=a&x=1") == "/"
    assert _safe_next("//evil.example") == "/"


def test_touch_targets_and_fonts_are_self_hosted(tmp_path: Path) -> None:
    c = _client(tmp_path, _calm())
    res = c.get("/", headers=GOOD)
    assert "font-src 'self'" in res.headers["content-security-policy"]
    assert "fonts.googleapis" not in res.text
    for name in re.findall(r"url\(/fonts/([\w.-]+)\)", res.text):
        font = c.get(f"/fonts/{name}", headers=GOOD)
        assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"
        assert font.headers["cache-control"] == "private, max-age=604800"
    assert c.get("/fonts/../app.py", headers=GOOD).status_code == 404
    assert c.get("/fonts/x.woff2", headers=GOOD).status_code == 404
    css = res.text.split("<style>", 1)[1].split("</style>", 1)[0]
    for cls in (".pc{", ".btn{", "input.nyi__reply{", "input.dbar__in{", ".dbar__go{", ".qchip{"):
        rule = css.split("\n" + cls, 1)[1].split("}", 1)[0]
        assert "min-height:44px" in rule, cls


# ---- quota chip ------------------------------------------------------------------


def _quota_json(five: float, seven: float) -> str:
    return json.dumps(
        {
            "schemaVersion": 5,
            "providers": [
                {"provider": "codex", "windows": [{"id": "five_hour", "percentRemaining": 1}]},
                {
                    "provider": "claude",
                    "windows": [
                        {
                            "id": "five_hour",
                            "percentRemaining": five,
                            "resetsAt": "2030-01-02T05:14:05Z",
                        },
                        {
                            "id": "seven_day",
                            "percentRemaining": seven,
                            "resetsAt": "2030-01-08T00:00:00Z",
                        },
                        {"id": "model:x", "percentRemaining": 0},
                    ],
                },
            ],
        }
    )


@pytest.mark.parametrize(
    ("five", "seven", "worst", "level"),
    [(82, 88, 82, "ok"), (29, 62, 29, "warn"), (40, 15, 15, "warn"), (7, 39, 7, "hot")],
)
def test_quota_is_the_worse_claude_window_as_percent_left(
    five: int, seven: int, worst: int, level: str
) -> None:
    q = parse_quota(_quota_json(five, seven))
    assert q is not None and [w.label for w in q.windows] == ["5-hour window", "7-day window"]
    assert (q.worst.left, q.level) == (worst, level)


def test_recorded_quota_axi_output_reads_as_what_is_left() -> None:
    """A real ``quota-axi --provider claude --json`` capture (2026-10-07): 34% / 47% / 9% left."""
    q = parse_quota((FIXTURE.parent / "quota-axi-claude.json").read_text())
    assert q is not None
    assert [(w.label, w.left) for w in q.windows] == [
        ("5-hour window", 34),
        ("7-day window", 47),
        ("Fable week", 9),
    ]
    assert q.worst.left == 34  # the Fable-only window does not drive the headline
    now = datetime(2026, 10, 7, 8, 24, tzinfo=UTC)
    html = _chip(Ctx("t", quota=q, tz="UTC"), now)
    assert "34% left</summary>" in html and "width:34%" in html
    five, week = "2026-10-07T12:29:59.584507+00:00", "2026-10-08T14:59:59.584532+00:00"
    fable = "2026-10-08T14:59:59.584750+00:00"

    def row(left: int, iso: str, shown: str) -> str:
        return f"<b>{left}% left · resets <time class=reset datetime='{iso}'>{shown}</time></b>"

    assert row(34, five, "12:29 pm") in html
    assert row(47, week, "Thu 2:59 pm") in html
    assert row(9, fable, "Thu 2:59 pm") in html
    local = _chip(Ctx("t", quota=q, tz="Australia/Sydney"), now)
    assert row(34, five, "11:29 pm") in local


@pytest.mark.parametrize("raw", ["nope", "{}", '{"providers":[{"provider":"claude"}]}'])
def test_quota_unknown_when_unreadable(raw: str) -> None:
    assert parse_quota(raw) is None


def test_chip_states() -> None:
    now = datetime(2030, 1, 2, 3, 4, tzinfo=UTC)
    q = Quota((Window("5-hour window", 29, datetime(2030, 1, 2, 5, 14, tzinfo=UTC)),))
    html = _chip(Ctx("t", quota=q, tz="UTC"), now)
    assert "class='qchip qchip--warn'" in html and "worst window 29 percent left" in html
    reset = "<time class=reset datetime='2030-01-02T05:14:00+00:00'>5:14 am</time>"
    assert f"<b>29% left · resets {reset}</b>" in html
    hot = Quota((Window("5-hour window", 7, now + timedelta(minutes=25)),))
    assert "qchip--hot" in _chip(Ctx("t", quota=hot), now)
    assert "qchip--unknown" in _chip(Ctx("t"))


def test_page_reads_quota_axi(tmp_path: Path) -> None:
    fake = _exe(tmp_path / "q" / "quota-axi", f"echo '{_quota_json(7, 39)}'")
    html = _client(tmp_path, _calm(), fake).get("/", headers=GOOD).text
    assert "class='qchip qchip--hot'" in html and ">7% left</summary>" in html
    # every desk page carries the chip in its chrome
    assert "id=qchip" in _client(tmp_path, _calm()).get("/p/alpha", headers=GOOD).text


async def test_quota_provider_serves_stale_while_refreshing(tmp_path: Path) -> None:
    counter = tmp_path / "n"
    fake = _exe(tmp_path / "quota-axi", f"echo x >> '{counter}'; echo '{_quota_json(50, 50)}'")
    settings = GatewaySettings(quota_axi=fake, quota_ttl_s=60)
    provider = QuotaProvider(settings)
    assert (await provider.get()).worst.left == 50
    assert (await provider.get()).worst.left == 50
    assert len(counter.read_text().splitlines()) == 1
    assert await QuotaProvider(GatewaySettings()).get() is None


async def test_quota_provider_does_not_hold_a_cold_page(tmp_path: Path) -> None:
    fake = _exe(tmp_path / "quota-axi", f"sleep 0.5; echo '{_quota_json(20, 30)}'")
    provider = QuotaProvider(GatewaySettings(quota_axi=fake, quota_first_wait_s=0.05))
    start = time.monotonic()
    assert await provider.get() is None
    assert time.monotonic() - start < 0.4
    for _ in range(100):
        await asyncio.sleep(0.05)
        if (q := await provider.get()) is not None:
            break
    assert q is not None and q.worst.left == 20


def test_home_waits_once_on_a_cold_slow_quota(tmp_path: Path) -> None:
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(_calm()))
    _exe(tmp_path / "bin" / "fm-fleet-snapshot.sh", f"cat '{snap}'")
    fake = _exe(tmp_path / "q" / "quota-axi", f"sleep 5; echo '{_quota_json(20, 30)}'")
    settings = GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=tmp_path,
        snapshot_ttl_s=0,
        quota_axi=fake,
        quota_first_wait_s=0.5,
    )
    client = TestClient(create_app(settings), client=("127.0.0.1", 5000))
    start = time.monotonic()
    html = client.get("/", headers=GOOD).text
    assert time.monotonic() - start < 0.9
    assert "qchip--unknown" in html


def test_quota_axi_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HIVE_GATEWAY_QUOTA_AXI", "off")
    assert GatewaySettings.from_env().quota_axi is None
    monkeypatch.setenv("HIVE_GATEWAY_QUOTA_AXI", str(tmp_path / "q"))
    assert GatewaySettings.from_env().quota_axi == tmp_path / "q"


def test_chat_thread_labels_a_delegated_goal() -> None:
    from hive.gateway.chat import ChatView, parse_receipts
    from hive.gateway.pages import render_chat

    body = "HIVE-WEB DELEGATE v1\nproject: hive\nto: second mate\n---\nGo\n"
    data = {"schema": "fm-inbox-receipts.v1", "pending": [{"id": "n1", "at": "t", "body": body}]}
    items, _ = parse_receipts(data)
    assert [r.kind for r in items] == ["delegate"]
    html = render_chat(ChatView(True, True, items, []), Ctx("t"))
    assert "<span class=tag>delegated</span>" in html


# ---- Review pages: open Lavish sessions ------------------------------------------

LAVISH = Path(__file__).parent.parent / "fixtures" / "gateway" / "lavish-state.json"


def _client_with_reviews(tmp_path: Path, state: Path | None) -> TestClient:
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(_calm()))
    _exe(tmp_path / "bin" / "fm-fleet-snapshot.sh", f"cat '{snap}'")
    settings = GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=tmp_path,
        snapshot_ttl_s=0,
        board_url="https://board.example.ts.net:8445",
        lavish_state=state,
    )
    return TestClient(create_app(settings), client=("127.0.0.1", 5000))


def test_review_pages_list_open_sessions_on_the_tailnet_url(tmp_path: Path) -> None:
    html = _client_with_reviews(tmp_path, LAVISH).get("/", headers=GOOD).text
    section = html.split("Review pages", 1)[1].split("</section>", 1)[0]
    assert section.startswith(" · 3")  # open only; the ended and malformed ones are left out
    hrefs = re.findall(
        r"<a class=rv href='([^']+)' target=_blank rel='noopener noreferrer'", section
    )
    # reply waiting first, then newest
    assert hrefs == [
        f"https://board.example.ts.net:8445/session/{k}"
        for k in ("aaaa1111aaaa1111", "bbbb2222bbbb2222", "cccc3333cccc3333")
    ]
    assert "127.0.0.1:4387" not in html and "dddd4444" not in html and "bad/key" not in html
    assert re.findall(r"<span class=rv__title>([^<]*)</span>", section) == [
        "pixel board",
        "ai example",
        "recon report",
    ]
    assert re.findall(r"<span class=rv__proj>([^<]*)</span>", section) == ["alpha", "alpha"]
    assert section.count("<span class=rv__reply>reply</span>") == 1


def test_review_title_prefers_the_html_title(tmp_path: Path) -> None:
    page = tmp_path / "x.html"
    page.write_text("<!doctype html><title> Pick the\n palette </title><body>")
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"sessions": {"k1": {"key": "k1", "file": str(page), "status": "open"}}})
    )
    html = _client_with_reviews(tmp_path, state).get("/", headers=GOOD).text
    assert "<span class=rv__title>Pick the palette</span>" in html


@pytest.mark.parametrize("raw", [None, "nope", "[]", '{"sessions": 3}'])
def test_review_pages_absent_when_state_missing_or_unreadable(
    tmp_path: Path, raw: str | None
) -> None:
    state = tmp_path / "state.json"
    if raw is not None:
        state.write_text(raw)
    assert "Review pages" not in _client_with_reviews(tmp_path, state).get("/", headers=GOOD).text
    assert "Review pages" not in _client_with_reviews(tmp_path, None).get("/", headers=GOOD).text


def test_many_reviews_fold_the_rest(tmp_path: Path) -> None:
    sessions = {
        f"k{i}": {"key": f"k{i}", "file": f"/w/p{i}.html", "status": "open"} for i in range(9)
    }
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"sessions": sessions}))
    html = _client_with_reviews(tmp_path, state).get("/", headers=GOOD).text
    assert "Review pages · 9" in html and "<summary>+3 more</summary>" in html
