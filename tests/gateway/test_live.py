"""Live slice: change watcher + alerts, SSE route, push store, live tail, PWA files."""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive.gateway import live, pages
from hive.gateway.app import create_app
from hive.gateway.desk import build_desk
from hive.gateway.live import LiveHub
from hive.gateway.push import Alert, PushService, valid_subscription
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import SnapshotProvider
from hive.gateway.tail import clean_output

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
GOOD = {"tailscale-user-login": OWNER, "host": HOST}
SUB = {
    "endpoint": "https://push.example.test/abc",
    "keys": {"p256dh": "BPk-abcdefghijklmnop", "auth": "authsecret123"},
}


def _script(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _home(tmp_path: Path, peek: str = "echo hello") -> Path:
    (tmp_path / "bin").mkdir()
    (tmp_path / "state").mkdir()
    _script(tmp_path / "bin" / "fm-fleet-snapshot.sh", f"cat '{FIXTURE}'")
    _script(tmp_path / "bin" / "fm-peek.sh", peek)
    return tmp_path


def _settings(home: Path, **kw) -> GatewaySettings:
    return GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=home,
        snapshot_ttl_s=0,
        data_dir=home / "data",
        **kw,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return _home(tmp_path)


@pytest.fixture
def client(home: Path) -> TestClient:
    return TestClient(create_app(_settings(home)), client=("127.0.0.1", 5000))


# ---- status files and alerts -------------------------------------------------------


def test_status_signature_moves_when_a_status_file_changes(tmp_path: Path) -> None:
    f = tmp_path / "a.status"
    f.write_text("working [at=1]: x\n")
    before = live.status_signature(tmp_path)
    f.write_text("working [at=1]: x\nblocked [at=2]: y\n")
    assert live.status_signature(tmp_path) != before
    assert live.status_signature(tmp_path / "missing") == ()


def test_status_alerts_only_for_blocked_and_failed(tmp_path: Path) -> None:
    (tmp_path / "a.status").write_text("working [at=1]: x\nblocked [at=2]: stuck on auth\n")
    (tmp_path / "b.status").write_text("done [at=3]: fine\n")
    (tmp_path / "c.status").write_text(
        "blocked [at=4]: old\nresolved [at=5]: ok\nfailed [at=6]: no\n"
    )
    keys = {a.key: a for a in live.status_alerts(tmp_path)}
    assert set(keys) == {"status:a:blocked:2", "status:c:failed:6"}
    assert keys["status:a:blocked:2"].body == "stuck on auth"


def test_need_alerts_cover_decision_hold_and_merge() -> None:
    desk = build_desk(json.loads(FIXTURE.read_text()))
    alerts = live.need_alerts(desk)
    assert len(alerts) == 3
    assert {a.title.split(":")[0] for a in alerts} == {
        "Decision waiting",
        "A question is waiting for you",
        "PR ready for review",
    }
    assert all(a.url.startswith("/p/") for a in alerts)


async def test_hub_baselines_then_alerts_once_and_publishes(tmp_path: Path) -> None:
    home = _home(tmp_path)
    settings = _settings(home, poll_interval_s=0.05, receipts_interval_s=0.05)
    sent: list[Alert] = []

    async def notify(a: Alert) -> None:
        sent.append(a)

    hub = LiveHub(settings, SnapshotProvider(settings), notify)
    q = hub.subscribe()
    (home / "state" / "w.status").write_text("working [at=1]: go\n")
    hub.start()
    try:
        await asyncio.sleep(0.4)
        assert sent == []  # baseline: nothing pushed for what already existed
        assert hub.clients == 1
        (home / "state" / "w.status").write_text("working [at=1]: go\nfailed [at=9]: boom\n")
        await asyncio.sleep(live.MIN_SNAPSHOT_GAP_S + 1)
        assert [a.key for a in sent] == ["status:w:failed:9"]
        await asyncio.sleep(0.3)
        assert len(sent) == 1  # one push per key
    finally:
        await hub.stop()
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert any(e.startswith("event: desk") for e in events)


# ---- routes ------------------------------------------------------------------------


def test_new_routes_keep_the_owner_checks(client: TestClient) -> None:
    for path in ("/events", "/push/key", "/sw.js", "/manifest.webmanifest", "/w/alpha-build"):
        assert client.get(path, headers={"host": HOST}).status_code == 403
        assert client.get(path, headers={**GOOD, "host": "evil.example"}).status_code == 403
    for path in ("/push/subscribe", "/push/unsubscribe"):
        assert client.post(path, headers={"host": HOST}, json=SUB).status_code == 403
    assert client.post("/events", headers=GOOD).status_code == 405


def test_pwa_files(client: TestClient) -> None:
    sw = client.get("/sw.js", headers=GOOD)
    assert sw.headers["content-type"].startswith("application/javascript")
    assert sw.headers["service-worker-allowed"] == "/"
    manifest = client.get("/manifest.webmanifest", headers=GOOD).json()
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        res = client.get(icon["src"], headers=GOOD)
        assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert client.get("/icons/../../x", headers=GOOD).status_code == 404
    html = client.get("/", headers=GOOD).text
    assert "rel=manifest" in html and "apple-touch-icon" in html and "id=alerts" in html
    csp = client.get("/", headers=GOOD).headers["content-security-policy"]
    assert "worker-src 'self'" in csp and "manifest-src 'self'" in csp


def test_script_hash_matches_csp(client: TestClient) -> None:
    csp = client.get("/", headers=GOOD).headers["content-security-policy"]
    assert pages.SCRIPT_CSP_HASH in csp


def test_push_subscribe_roundtrip(client: TestClient, home: Path) -> None:
    key = client.get("/push/key", headers=GOOD).json()
    assert key["enabled"] and len(key["key"]) > 80
    pem = home / "data" / "vapid-private.pem"
    assert pem.is_file() and pem.stat().st_mode & 0o777 == 0o600
    csrf = {**GOOD, "origin": f"https://{HOST}", "x-csrf": key["csrf"]}
    assert (
        client.post("/push/subscribe", headers={**csrf, "x-csrf": "bad"}, json=SUB).status_code
        == 403
    )
    bad = {"endpoint": "http://insecure.example/x", "keys": SUB["keys"]}
    assert client.post("/push/subscribe", headers=csrf, json=bad).status_code == 403
    assert client.post("/push/subscribe", headers=csrf, json=SUB).json() == {"ok": True}
    subs = home / "data" / "subscriptions.json"
    assert json.loads(subs.read_text())[0]["endpoint"] == SUB["endpoint"]
    assert subs.stat().st_mode & 0o777 == 0o600
    res = client.post("/push/unsubscribe", headers=csrf, json={"endpoint": SUB["endpoint"]})
    assert res.json() == {"ok": True} and json.loads(subs.read_text()) == []
    cross = {**csrf, "sec-fetch-site": "cross-site"}
    assert client.post("/push/subscribe", headers=cross, json=SUB).status_code == 403


def test_vapid_key_is_stable_and_subscription_validation(tmp_path: Path) -> None:
    a = PushService(tmp_path, "mailto:o@example.test").public_key()
    assert PushService(tmp_path, "mailto:o@example.test").public_key() == a
    assert valid_subscription(SUB) == SUB
    assert valid_subscription({"endpoint": "https://x.test/", "keys": {}}) is None
    assert valid_subscription("nope") is None


# ---- live tail ---------------------------------------------------------------------


def test_clean_output_strips_escapes() -> None:
    raw = "\x1b[31mred\x1b[0m line\r\nnext\x07\x00 <b>x</b>"
    assert clean_output(raw) == "red line\nnext   <b>x</b>"


def test_watch_page_and_output(tmp_path: Path) -> None:
    home = _home(tmp_path, "printf '\\033[1mbuild <b>ok</b>\\033[0m %s lines\\n' \"$2\"")
    c = TestClient(create_app(_settings(home)), client=("127.0.0.1", 5000))
    crew = c.get("/p/alpha", headers=GOOD).text
    assert "Watch live" in crew
    page = c.get("/w/alpha-build", headers=GOOD)
    assert page.status_code == 200 and "data-tail='alpha-build'" in page.text
    assert "<b>ok</b>" not in page.text
    out = c.get("/w/alpha-build/out", headers=GOOD).json()
    assert out["ok"] and out["text"] == "build <b>ok</b> 60 lines\n"  # escaped client-side
    assert c.get("/w/not-a-task", headers=GOOD).status_code == 404
    assert c.get("/w/not-a-task/out", headers=GOOD).status_code == 404
    assert c.get("/w/..%2Fx/out", headers=GOOD).status_code == 404


def test_watch_output_reports_peek_failure(tmp_path: Path) -> None:
    home = _home(tmp_path, "echo no such pane >&2; exit 1")
    c = TestClient(create_app(_settings(home)), client=("127.0.0.1", 5000))
    res = c.get("/w/alpha-build/out", headers=GOOD)
    assert res.status_code == 502 and res.json()["ok"] is False


def test_origin_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    s = GatewaySettings.from_env()
    assert s.origin == "https://desktop-lfme032.tailfb3900.ts.net:8446"
    assert "desktop-lfme032.tailfb3900.ts.net" in s.allowed_hosts
    monkeypatch.setenv("HIVE_GATEWAY_ORIGIN", "https://box.example.ts.net:9443/")
    s = GatewaySettings.from_env()
    assert s.origin == "https://box.example.ts.net:9443"
    assert s.allowed_hosts == ("box.example.ts.net", "localhost", "127.0.0.1")


async def test_push_notify_sends_to_each_subscription_and_prunes_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pywebpush import WebPushException

    svc = PushService(tmp_path, "mailto:o@example.test")
    gone = {"endpoint": "https://push.example.test/gone", "keys": SUB["keys"]}
    svc.add(SUB)
    svc.add(gone)
    calls: list[tuple[str, dict]] = []

    class Resp:
        status_code = 410

    def fake(subscription_info, data, **kw):
        calls.append((subscription_info["endpoint"], json.loads(data)))
        if subscription_info["endpoint"].endswith("gone"):
            raise WebPushException("gone", response=Resp())

    monkeypatch.setattr("hive.gateway.push.webpush", fake)
    sent = await svc.notify(Alert("k1", "Decision waiting: x", "pick", "/p/alpha"))
    assert sent == 1 and len(calls) == 2
    assert calls[0][1] == {
        "title": "Decision waiting: x",
        "body": "pick",
        "url": "/p/alpha",
        "tag": "k1",
    }
    assert [s["endpoint"] for s in svc.subscriptions()] == [SUB["endpoint"]]
