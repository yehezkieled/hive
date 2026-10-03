"""Gateway contract: auth, pinned snapshot schema, fixture-backed pages, fallback."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive.gateway.app import create_app
from hive.gateway.desk import build_desk
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import SnapshotProvider, parse_snapshot

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
GOOD = {"tailscale-user-login": OWNER, "host": HOST}


def _settings(fm_home: Path) -> GatewaySettings:
    return GatewaySettings(
        owner_login=OWNER, allowed_hosts=(HOST,), fm_home=fm_home, snapshot_ttl_s=0
    )


def _fake_home(tmp_path: Path, body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "fm-fleet-snapshot.sh"
    script.write_text(f"#!/usr/bin/env bash\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return tmp_path


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    home = _fake_home(tmp_path, f"cat '{FIXTURE}'")
    app = create_app(_settings(home))
    return TestClient(app, client=("127.0.0.1", 5000))


def test_fixture_is_synthetic_and_pinned() -> None:
    raw = FIXTURE.read_text()
    assert "/home/" not in raw and "hezki" not in raw
    assert parse_snapshot(raw).ok


@pytest.mark.parametrize(
    "headers",
    [
        {"host": HOST},
        {"tailscale-user-login": "intruder@example.test", "host": HOST},
        {"tailscale-user-login": OWNER, "host": "evil.example"},
        {**GOOD, "origin": "https://evil.example"},
    ],
)
def test_auth_failures_are_403(client: TestClient, headers: dict) -> None:
    assert client.get("/", headers=headers).status_code == 403
    assert client.get("/p/alpha", headers=headers).status_code == 403


def test_non_loopback_peer_is_403(tmp_path: Path) -> None:
    app = create_app(_settings(_fake_home(tmp_path, f"cat '{FIXTURE}'")))
    remote = TestClient(app, client=("100.64.0.9", 5000))
    assert remote.get("/", headers=GOOD).status_code == 403


def test_owner_with_matching_origin_and_port_ok(client: TestClient) -> None:
    headers = {**GOOD, "host": f"{HOST}:8446", "origin": f"https://{HOST}:8446"}
    assert client.get("/", headers=headers).status_code == 200


def test_writes_refused_after_auth(client: TestClient) -> None:
    assert client.post("/", headers=GOOD).status_code == 405
    assert client.post("/", headers={"host": HOST}).status_code == 403
    assert client.put("/act/chat", headers=GOOD).status_code == 405


def test_home_page_from_fixture(client: TestClient) -> None:
    res = client.get("/", headers=GOOD)
    assert res.status_code == 200
    html = res.text
    assert "Needs you (3)" in html  # hold + decision + merge approval
    assert "merge approval needed" in html and "Choose option A or B" in html
    assert "alpha" in html and "beta" in html
    assert "General" in html and "(no project)" not in html
    assert "<b>chore</b>" not in html
    assert res.headers["cache-control"] == "no-store"
    assert html.count("<script") == 1  # the one pinned inline script
    assert "<a href='/chat'" in html and "Message the first mate" in html
    needs = html.split("Needs you (3)", 1)[1].split("<h2>Projects</h2>", 1)[0]
    leads = re.findall(r"<div class=need><span class=what>([^<]*)</span>", needs)
    subs = re.findall(r"<span class=sub>([^<]*)</span>", needs)
    assert len(leads) == len(subs) == 3
    assert any(lead.endswith("Build the alpha widget") for lead in leads)
    assert not any("alpha-build" in lead for lead in leads)
    assert any("alpha-build" in sub for sub in subs)


def test_referrer_policy_keeps_origin_on_same_origin_posts(client: TestClient) -> None:
    # Fetch spec: under "no-referrer" a browser serialises a form POST's Origin
    # as "null", which the Origin check refuses, so no action could ever run.
    # "same-origin" sends the real Origin to us and no referrer to other sites.
    res = client.get("/", headers=GOOD)
    assert res.headers["referrer-policy"] == "same-origin"


def test_project_page_from_fixture(client: TestClient) -> None:
    html = client.get("/p/alpha", headers=GOOD).text
    assert "Build the alpha widget" in html and "blocked by Build the alpha widget" in html
    assert "merge approval needed" in html
    assert "Pick the beta palette" not in html


def test_unknown_project_404(client: TestClient) -> None:
    assert client.get("/p/nope", headers=GOOD).status_code == 404


def test_schema_major_pin() -> None:
    data = json.loads(FIXTURE.read_text())
    data["schema"] = "fm-fleet-snapshot.v2"
    snap = parse_snapshot(json.dumps(data))
    assert not snap.ok and "newer" in (snap.reason or "")
    data["schema"] = "fm-fleet-snapshot.v1"
    data["added_in_future"] = {"x": 1}
    assert parse_snapshot(json.dumps(data)).ok


@pytest.mark.parametrize(
    "body",
    ["echo not-json", "exit 3", 'echo \'{"schema":"fm-fleet-snapshot.v9","generated":"t"}\''],
)
def test_fallback_when_snapshot_unusable(tmp_path: Path, body: str) -> None:
    app = create_app(_settings(_fake_home(tmp_path, body)))
    c = TestClient(app, client=("127.0.0.1", 5000))
    for path in ("/", "/p/alpha"):
        res = c.get(path, headers=GOOD)
        assert res.status_code == 200
        assert "Read-only fallback" in res.text
        assert "<form" not in res.text


def test_missing_script_falls_back(tmp_path: Path) -> None:
    c = TestClient(create_app(_settings(tmp_path)), client=("127.0.0.1", 5000))
    assert "Read-only fallback" in c.get("/", headers=GOOD).text


def test_desk_tolerates_missing_fields() -> None:
    desk = build_desk({"schema": "fm-fleet-snapshot.v1", "backlog": None, "tasks": [None, {}]})
    assert desk.needs_you == []


def _mate_snapshot() -> dict:
    return {
        "schema": "fm-fleet-snapshot.v1",
        "secondmate_current": {
            "records": [
                {
                    "id": "hive",
                    "queued": [{"id": "q1", "title": "Queued", "repo": "/r/hive"}],
                    "active_children": [
                        {"id": "w1", "name": "Working", "repo": "/r/hive", "state": "working"}
                    ],
                    "decisions_open": [
                        {"id": "h1", "verb": "captain-hold", "summary": "Pick", "reason": "A?"},
                        {"id": "w1", "key": "k", "verb": "needs-decision", "summary": "Q?"},
                    ],
                    "holds": [
                        {"id": "q1", "title": "Queued", "reason": "blocked", "source": "backlog"},
                        {
                            "id": "p1",
                            "title": "Parked",
                            "reason": "paused",
                            "source": "child-state",
                        },
                    ],
                    "counts": {
                        "queued": 4,
                        "active_children": 1,
                        "decisions_open": 2,
                        "holds": 3,
                    },
                }
            ]
        },
    }


def test_desk_reads_every_ticket_a_mate_owns_and_counts_the_cut_off_rest() -> None:
    desk = build_desk(_mate_snapshot())
    rows = {r.id: (r.state, r.owner) for p in desk.projects.values() for r in p.rows}
    assert rows == {
        "q1": ("queued", "hive"),
        "w1": ("in_flight", "hive"),
        "h1": ("queued", "hive"),
        "p1": ("in_flight", "hive"),
    }
    assert [(n.kind, n.ref, n.owner) for n in desk.needs_you] == [("hold", "h1", "hive")]
    assert desk.more == {"hive": 3}


def test_home_shows_how_many_mate_tickets_were_cut_off(tmp_path: Path) -> None:
    home = _fake_home(tmp_path, f"cat <<'EOF'\n{json.dumps(_mate_snapshot())}\nEOF")
    settings = GatewaySettings(owner_login=OWNER, allowed_hosts=(HOST,), fm_home=home)
    c = TestClient(create_app(settings), client=("127.0.0.1", 5000))
    assert "+3 more owned by hive" in c.get("/", headers=GOOD).text


async def test_provider_caches(tmp_path: Path) -> None:
    counter = tmp_path / "n"
    home = _fake_home(tmp_path, f"echo x >> '{counter}'; cat '{FIXTURE}'")
    settings = GatewaySettings(
        owner_login=OWNER, allowed_hosts=(HOST,), fm_home=home, snapshot_ttl_s=60
    )
    provider = SnapshotProvider(settings)
    await provider.get()
    await provider.get()
    assert len(counter.read_text().splitlines()) == 1


def test_cards_are_plain_language_and_board_links_rewritten(tmp_path: Path) -> None:
    data = json.loads(FIXTURE.read_text())
    for task in data["tasks"]:
        for dec in task.get("hints", {}).get("open_decisions", []):
            dec["summary"] = "pick one; board http://127.0.0.1:4387/session/abc123"
    home = _fake_home(tmp_path, f"cat <<'EOF'\n{json.dumps(data)}\nEOF")
    settings = GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=home,
        snapshot_ttl_s=0,
        board_url="https://board.example.ts.net:8445/",
    )
    c = TestClient(create_app(settings), client=("127.0.0.1", 5000))
    res = c.get("/", headers=GOOD)
    html = res.text
    assert "127.0.0.1:4387" not in html
    assert 'href="https://board.example.ts.net:8445/session/abc123"' in html
    assert "Pick the beta palette" in html.split("Projects")[0]  # human title leads the card
    assert "script-src 'sha256-" in res.headers["content-security-policy"]


def test_times_are_local_not_utc(client: TestClient) -> None:
    html = client.get("/", headers=GOOD).text
    assert "<time datetime='2030-01-02T03:04:05+00:00'>Wed 2 Jan, 2:04 PM</time>" in html
    assert "03:04:05Z" not in html


def test_header_links_chat_on_every_page(client: TestClient) -> None:
    for path in ("/", "/p/alpha", "/chat"):
        assert "<a href='/chat'" in client.get(path, headers=GOOD).text


def test_auto_refresh_hooks(client: TestClient) -> None:
    assert "data-refresh=30000" in client.get("/", headers=GOOD).text
    assert "data-refresh=30000" in client.get("/p/alpha", headers=GOOD).text
