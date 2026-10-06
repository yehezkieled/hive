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
    assert "<b>Waiting on you</b> — 2 items (see Needs you)" in html


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


def test_calm_hero_counts_running_loops(tmp_path: Path) -> None:
    html = _client(tmp_path, _calm()).get("/", headers=GOOD).text
    assert "class='nyl nyl--calm'" in html and "<span class=nyl__count>0</span>" in html
    assert "✓ all clear · 2 loops running" in html and "nothing needs you" in html
    assert _cards(html) == {"alpha": "running", "beta": "idle", "hive": "running"}
    assert "<span class=pc__mae>second mate</span>" in html
    assert "1 / 3 tasks" in html and ">done<span class=pc__open>" in html


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
    for cls in (".pc{", ".btn{", ".nyi__reply{", ".dbar__in{", ".dbar__go{", ".qchip{"):
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
    [(82, 88, 18, "ok"), (29, 62, 71, "warn"), (40, 15, 85, "warn"), (7, 39, 93, "hot")],
)
def test_quota_is_the_worse_claude_window(five: int, seven: int, worst: int, level: str) -> None:
    q = parse_quota(_quota_json(five, seven))
    assert q is not None and [w.label for w in q.windows] == ["5-hour window", "7-day window"]
    assert (q.worst.used, q.level) == (worst, level)


@pytest.mark.parametrize("raw", ["nope", "{}", '{"providers":[{"provider":"claude"}]}'])
def test_quota_unknown_when_unreadable(raw: str) -> None:
    assert parse_quota(raw) is None


def test_chip_shows_worst_and_both_windows() -> None:
    q = Quota(
        (
            Window("5-hour window", 71, datetime(2030, 1, 2, 5, 14, tzinfo=UTC)),
            Window("7-day window", 38, datetime(2030, 1, 8, tzinfo=UTC)),
        )
    )
    now = datetime(2030, 1, 2, 3, 4, tzinfo=UTC)
    html = _chip(Ctx("t", quota=q, tz="UTC"), now)
    assert "class='qchip qchip--warn'" in html and "worst window 71 percent" in html
    assert "<b>71% · resets 2h 10m</b>" in html and "<b>38% · resets Tue</b>" in html
    soon = Quota((Window("5-hour window", 93, now + timedelta(minutes=25)),))
    assert "93% · resets 25m" in _chip(Ctx("t", quota=soon), now)
    assert "qchip--unknown" in _chip(Ctx("t"))


def test_page_reads_quota_axi(tmp_path: Path) -> None:
    fake = _exe(tmp_path / "q" / "quota-axi", f"echo '{_quota_json(7, 39)}'")
    html = _client(tmp_path, _calm(), fake).get("/", headers=GOOD).text
    assert "class='qchip qchip--hot'" in html and ">93%</summary>" in html
    # every desk page carries the chip in its chrome
    assert "id=qchip" in _client(tmp_path, _calm()).get("/p/alpha", headers=GOOD).text


async def test_quota_provider_serves_stale_while_refreshing(tmp_path: Path) -> None:
    counter = tmp_path / "n"
    fake = _exe(tmp_path / "quota-axi", f"echo x >> '{counter}'; echo '{_quota_json(50, 50)}'")
    settings = GatewaySettings(quota_axi=fake, quota_ttl_s=60)
    provider = QuotaProvider(settings)
    assert (await provider.get()).worst.used == 50
    assert (await provider.get()).worst.used == 50
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
    assert q is not None and q.worst.used == 80


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
