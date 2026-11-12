"""The gone-dark alarm's sender: config, the bare GET, the schedule, skips, secrecy, lock.

Nothing here leaves the machine: the ping goes to a fake HTTPS server on loopback with
a throwaway self-signed certificate, the clock is fake where the schedule is tested,
and the desk and firstmate checks are fakes or loopback sockets.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import logging
import os
import signal
import ssl
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from hive import gonedark
from hive.gateway.wake import FirstmateStatus

ROOT = Path(__file__).resolve().parents[1]
# A recognisable secret: it must never show up in any output, log line or file but the config.
CANARY = "canary-7f3a9e51c2d84b6a"


# ---- a fake HTTPS ping service -----------------------------------------------------------


@pytest.fixture(scope="module")
def tls(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A throwaway self-signed certificate for 127.0.0.1: (cert file, key file)."""
    d = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = d / "cert.pem", d / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class PingService:
    port: int
    requests: list[Seen] = field(default_factory=list)
    connections: int = 0
    status: int = 200
    delay_s: float = 0.0
    log: list[str] = field(default_factory=list)

    def url(self, path: str = f"/ping/{CANARY}") -> str:
        return f"https://127.0.0.1:{self.port}{path}"


@contextmanager
def ping_service(tls: tuple[Path, Path]) -> Iterator[PingService]:
    svc = PingService(0)

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            svc.connections += 1
            super().setup()

        def _any(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            svc.requests.append(
                Seen(self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body)
            )
            if svc.delay_s:
                time.sleep(svc.delay_s)
            self.send_response(svc.status)
            if 300 <= svc.status < 400:
                self.send_header("Location", f"https://127.0.0.1:{svc.port}/elsewhere")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

        do_GET = do_POST = do_HEAD = _any  # noqa: N815

        def log_message(self, fmt: str, *args: object) -> None:
            svc.log.append(fmt % args)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*tls)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    svc.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield svc
    finally:
        server.shutdown()
        server.server_close()


def client_ctx(tls: tuple[Path, Path]) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(tls[0]))


# ---- config ------------------------------------------------------------------------------


def test_off_without_an_address() -> None:
    with pytest.raises(gonedark.GoneDarkConfigError, match="not set"):
        gonedark.load_config({})


def test_config_defaults_to_five_minutes() -> None:
    c = gonedark.load_config({"HIVE_GONEDARK_URL": "https://hc-ping.example/abc-123"})
    assert c.interval_s == 300
    assert "abc-123" not in repr(c) and "hc-ping" not in repr(c)


@pytest.mark.parametrize(
    "url",
    [
        "https://hc-ping.example/0f0e-uuid",
        "https://ping.example:8443/p/a_b~c.d?x=1&y=2#f",
        "https://127.0.0.1/ok",
        "https://example.test",
    ],
)
def test_https_addresses_are_accepted(url: str) -> None:
    assert gonedark.load_config({"HIVE_GONEDARK_URL": url}).url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://hc-ping.example/abc",
        "HTTPS://hc-ping.example/abc",
        "ftp://hc-ping.example/abc",
        "hc-ping.example/abc",
        "https://",
        "https://user:pass@hc-ping.example/abc",
        "https://hc-ping.example/a b",
        "https://hc-ping.example/$(touch pwned)",
        "https://hc-ping.example/a'b",
        'https://hc-ping.example/a"b',
        "https://hc-ping.example/a\nb",
        "https://hc-ping.example/" + "a" * 2100,
    ],
)
def test_anything_but_a_plain_https_address_is_refused(url: str) -> None:
    with pytest.raises(gonedark.GoneDarkConfigError) as err:
        gonedark.load_config({"HIVE_GONEDARK_URL": url})
    assert "hc-ping" not in str(err.value)


@pytest.mark.parametrize("value", ["59", "3601", "abc", "-5", "300.5"])
def test_interval_bounds(value: str) -> None:
    env = {"HIVE_GONEDARK_URL": "https://hc-ping.example/x", "HIVE_GONEDARK_INTERVAL_S": value}
    with pytest.raises(gonedark.GoneDarkConfigError, match="60 to 3600"):
        gonedark.load_config(env)


def test_main_with_no_config_exits_78_and_sends_nothing(capfd: pytest.CaptureFixture) -> None:
    assert gonedark.main({}) == gonedark.EX_CONFIG
    assert "not starting: HIVE_GONEDARK_URL is not set" in capfd.readouterr().err


# ---- the ping ----------------------------------------------------------------------------


async def test_the_ping_is_one_bare_get(tls: tuple[Path, Path]) -> None:
    with ping_service(tls) as svc:
        assert await gonedark.https_ping(svc.url(), verify=client_ctx(tls))() is None
    assert svc.connections == 1 and len(svc.requests) == 1
    req = svc.requests[0]
    assert req.method == "GET" and req.path == f"/ping/{CANARY}" and req.body == b""
    # Only what any bare GET carries: no cookie, no auth, no body, no machine data.
    assert set(req.headers) <= {"host", "accept", "accept-encoding", "connection", "user-agent"}
    assert req.headers["user-agent"] == "hive-gonedark"
    assert req.headers["host"] == f"127.0.0.1:{svc.port}"
    assert "content-length" not in req.headers


async def test_proxy_and_netrc_settings_are_ignored(
    tls: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    netrc = tmp_path / ".netrc"
    netrc.write_text("machine 127.0.0.1 login someone password hunter2\n")
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")  # would fail if it were used
    with ping_service(tls) as svc:
        assert await gonedark.https_ping(svc.url(), verify=client_ctx(tls))() is None
    assert "authorization" not in svc.requests[0].headers


async def test_an_untrusted_certificate_is_not_sent_to(tls: tuple[Path, Path]) -> None:
    with ping_service(tls) as svc:
        assert await gonedark.https_ping(svc.url())() == gonedark.NOT_SENT
    assert svc.requests == []


@pytest.mark.parametrize(
    ("status", "want"), [(204, None), (302, gonedark.REDIRECTED), (404, gonedark.REFUSED)]
)
async def test_answers(tls: tuple[Path, Path], status: int, want: str | None) -> None:
    with ping_service(tls) as svc:
        svc.status = status
        assert await gonedark.https_ping(svc.url(), verify=client_ctx(tls))() == want
    assert len(svc.requests) == 1  # a redirect is never followed


async def test_a_slow_service_times_out(tls: tuple[Path, Path]) -> None:
    assert gonedark.REQUEST_TIMEOUT_S < 10
    with ping_service(tls) as svc:
        svc.delay_s = 3.0
        began = time.monotonic()
        got = await gonedark.https_ping(svc.url(), timeout_s=0.5, verify=client_ctx(tls))()
        took = time.monotonic() - began
    assert got == gonedark.TIMED_OUT and took < 2.0


async def test_nothing_listening_is_a_quiet_failure() -> None:
    assert await gonedark.https_ping("https://127.0.0.1:9/x", timeout_s=2)() == gonedark.NOT_SENT


# ---- the health check --------------------------------------------------------------------


@contextmanager
def fake_desk() -> Iterator[int]:
    """A loopback port that answers HTTP the way the desk does."""

    class Desk(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_a: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Desk)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _status(pids: tuple[int, ...]):
    def read(_home: Path) -> FirstmateStatus:
        return FirstmateStatus("alive" if pids else "down", pids, 5.0, 0.0)

    return read


async def test_health_needs_the_desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gonedark, "read_status", _status((4242,)))
    with fake_desk() as port:
        pass  # closed again: nothing answers there now
    assert await gonedark.local_health(port, tmp_path, None)() == gonedark.DESK_DOWN


async def test_health_needs_a_firstmate_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with fake_desk() as port:
        monkeypatch.setattr(gonedark, "read_status", _status(()))
        assert await gonedark.local_health(port, tmp_path, None)() == gonedark.NO_FIRSTMATE
        monkeypatch.setattr(gonedark, "read_status", _status((4242,)))
        assert await gonedark.local_health(port, tmp_path, None)() is None


async def test_an_unreadable_proc_is_not_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_home: Path) -> FirstmateStatus:
        raise OSError("no /proc")

    monkeypatch.setattr(gonedark, "read_status", broken)
    with fake_desk() as port:
        assert await gonedark.local_health(port, tmp_path, None)() == gonedark.CHECK_FAILED


def _tailscale(tmp_path: Path, answer: str) -> Path:
    binary = tmp_path / "tailscale"
    binary.write_text(f"#!/bin/sh\ncat <<'EOF'\n{answer}\nEOF\n")
    binary.chmod(0o755)
    return binary


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ('{"BackendState": "NeedsLogin", "Self": {"Online": false}}', gonedark.TAILSCALE_DOWN),
        ('{"BackendState": "Stopped", "Self": {"Online": false}}', gonedark.TAILSCALE_DOWN),
        ('{"BackendState": "Running", "Self": {"Online": false}}', gonedark.TAILSCALE_DOWN),
        ('{"BackendState": "Running", "Self": {"Online": true}}', None),
        ("not json", None),
    ],
    ids=["logged-out", "stopped", "offline", "connected", "unreadable"],
)
async def test_health_needs_tailscale_connected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, reason: str | None
) -> None:
    monkeypatch.setattr(gonedark, "read_status", _status((4242,)))
    binary = _tailscale(tmp_path, answer)
    with fake_desk() as port:
        assert await gonedark.local_health(port, tmp_path, binary)() == reason


async def test_a_missing_tailscale_command_still_pings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gonedark, "read_status", _status((4242,)))
    with fake_desk() as port:
        assert await gonedark.local_health(port, tmp_path, None)() is None


async def test_a_signed_out_tailscale_skips_the_ping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gonedark, "read_status", _status((4242,)))
    binary = _tailscale(tmp_path, '{"BackendState": "NeedsLogin"}')
    pings: list[str] = []

    async def ping() -> str | None:
        pings.append("ping")
        return None

    with fake_desk() as port:
        sender = gonedark.Sender(
            gonedark.local_health(port, tmp_path, binary), ping, tmp_path / "state", 60
        )
        outcome = await sender.tick()
    assert (outcome.result, outcome.reason) == (gonedark.SKIPPED, gonedark.TAILSCALE_DOWN)
    assert pings == []
    assert gonedark.read_last(tmp_path / "state") == outcome


# ---- the sender: schedule, skips, records --------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = 1_800_000_000.0

    def monotonic(self) -> float:
        return self.mono

    def time(self) -> float:
        return self.wall

    def advance(self, s: float) -> None:
        self.mono += s
        self.wall += s


@dataclass
class Rig:
    clock: FakeClock
    sender: gonedark.Sender
    pings: list[float]
    waits: list[float]
    health: list[str | None]
    answers: list[str | None]
    ping_takes_s: float = 0.0


def rig(tmp_path: Path, interval: float = 300) -> Rig:
    clock = FakeClock()
    r = Rig(clock, None, [], [], [None], [None])  # type: ignore[arg-type]

    async def health() -> str | None:
        return r.health[0]

    async def ping() -> str | None:
        r.pings.append(clock.monotonic())
        clock.advance(r.ping_takes_s)
        return r.answers[0]

    r.sender = gonedark.Sender(
        health, ping, tmp_path / "state", interval, clock.time, clock.monotonic
    )
    return r


async def run_ticks(r: Rig, ticks: int) -> None:
    """Run the real loop for ``ticks`` ticks; waiting only moves the fake clock."""
    stop = asyncio.Event()
    real = r.sender.tick
    done = 0

    async def counted() -> gonedark.Outcome:
        nonlocal done
        outcome = await real()
        done += 1
        if done >= ticks:
            stop.set()
        return outcome

    async def wait(seconds: float) -> None:
        r.waits.append(seconds)
        r.clock.advance(seconds)

    r.sender.tick = counted  # type: ignore[method-assign]
    await r.sender.run(stop, wait)


async def test_pings_now_and_then_every_interval(tmp_path: Path) -> None:
    r = rig(tmp_path)
    await run_ticks(r, 4)
    assert r.pings == [1000.0, 1300.0, 1600.0, 1900.0]
    assert r.waits == [300.0, 300.0, 300.0]


async def test_a_slow_ping_never_causes_a_burst(tmp_path: Path) -> None:
    r = rig(tmp_path, interval=60)
    r.ping_takes_s = 8.0  # each tick eats part of its slot
    await run_ticks(r, 3)
    assert r.waits == [52.0, 52.0]  # the schedule holds: one ping per 60 s
    r2 = rig(tmp_path, interval=60)
    r2.ping_takes_s = 90.0  # a tick longer than its slot (e.g. the PC slept)
    await run_ticks(r2, 3)
    assert r2.waits == [60.0, 60.0]  # counted again from now, no catch-up
    assert [b - a for a, b in zip(r2.pings, r2.pings[1:], strict=False)] == [150.0, 150.0]


async def test_a_failed_ping_waits_for_the_next_tick(tmp_path: Path) -> None:
    r = rig(tmp_path)
    r.answers[0] = gonedark.TIMED_OUT
    await run_ticks(r, 3)
    assert len(r.pings) == 3 and r.waits == [300.0, 300.0]  # no retries in between
    last = gonedark.read_last(tmp_path / "state")
    assert last == gonedark.Outcome(r.clock.wall, gonedark.FAILED, gonedark.TIMED_OUT)


async def test_unhealthy_skips_the_ping_and_says_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    r = rig(tmp_path)
    r.health[0] = gonedark.DESK_DOWN
    with caplog.at_level(logging.INFO, "hive.gonedark"):
        await run_ticks(r, 2)
    assert r.pings == []
    assert "skipped the ping: the desk is not answering" in caplog.text
    last = gonedark.read_last(tmp_path / "state")
    assert last is not None and (last.result, last.reason) == (gonedark.SKIPPED, gonedark.DESK_DOWN)


async def test_recovery_is_one_line_not_one_per_tick(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    r = rig(tmp_path)
    with caplog.at_level(logging.INFO, "hive.gonedark"):
        await run_ticks(r, 4)
    assert caplog.text.count("ping sent") == 1


async def test_a_raising_check_or_ping_is_a_quiet_outcome(tmp_path: Path) -> None:
    async def boom() -> str | None:
        raise RuntimeError(f"https://x/{CANARY}")

    s = gonedark.Sender(boom, boom, tmp_path, 300)
    assert (await s.tick()).reason == gonedark.CHECK_FAILED

    async def fine() -> str | None:
        return None

    s = gonedark.Sender(fine, boom, tmp_path, 300)
    assert (await s.tick()).reason == gonedark.NOT_SENT


async def test_the_state_file_is_private_and_holds_no_address(tmp_path: Path) -> None:
    r = rig(tmp_path)
    await run_ticks(r, 1)
    f = tmp_path / "state" / gonedark.STATE_FILE
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    assert set(json.loads(f.read_text())) == {"at", "result", "reason", "interval_s"}
    assert gonedark.read_interval(tmp_path / "state") == 300


@pytest.mark.parametrize(
    "text",
    [
        "",
        "garbage",
        "[]",
        '{"at": 1, "result": "exploded", "reason": ""}',
        '{"at": 1, "result": "failed", "reason": "https://evil.example/see-this"}',
        '{"at": "soon", "result": "sent", "reason": ""}',
    ],
)
def test_a_state_file_not_understood_reads_none(tmp_path: Path, text: str) -> None:
    (tmp_path / gonedark.STATE_FILE).write_text(text)
    assert gonedark.read_last(tmp_path) is None


# ---- single instance ---------------------------------------------------------------------


def test_one_instance_per_state_dir(tmp_path: Path) -> None:
    first = gonedark.take_lock(tmp_path)
    assert first is not None
    assert gonedark.take_lock(tmp_path) is None
    first.close()
    again = gonedark.take_lock(tmp_path)
    assert again is not None
    again.close()


def test_a_second_sender_exits_without_pinging(
    tmp_path: Path, tls: tuple[Path, Path], capfd: pytest.CaptureFixture
) -> None:
    held = gonedark.take_lock(tmp_path)
    try:
        with ping_service(tls) as svc:
            env = {"HIVE_GONEDARK_URL": svc.url(), "HIVE_GONEDARK_STATE_DIR": str(tmp_path)}
            assert gonedark.main(env) == 0
        assert svc.connections == 0
    finally:
        assert held is not None
        held.close()
    err = capfd.readouterr().err
    assert "already runs" in err and CANARY not in err


# ---- the address never leaks -------------------------------------------------------------


async def test_canary_never_reaches_logs_state_or_errors(
    tmp_path: Path, tls: tuple[Path, Path], capfd: pytest.CaptureFixture
) -> None:
    gonedark.setup_logging()
    try:
        with ping_service(tls) as svc:
            config = gonedark.load_config({"HIVE_GONEDARK_URL": svc.url()})
            gonedark.setup_logging(config.url)
            logging.getLogger("httpx").warning("request to %s", config.url)  # belt and braces
            healthy = True

            async def health() -> str | None:
                return None if healthy else gonedark.NO_FIRSTMATE

            ok = gonedark.https_ping(config.url, verify=client_ctx(tls))
            sender = gonedark.Sender(health, ok, tmp_path, config.interval_s)
            await sender.tick()  # sent
            svc.status = 500
            await sender.tick()  # refused
            svc.status, svc.delay_s = 200, 2.0
            sender._ping = gonedark.https_ping(config.url, 0.3, client_ctx(tls))
            await sender.tick()  # timed out
            sender._ping = gonedark.https_ping(config.url)  # certificate not trusted
            await sender.tick()
            healthy = False
            await sender.tick()  # skipped
            logging.getLogger("hive.gonedark").exception("oops %r %s", config, config.url)
    finally:
        logging.getLogger().handlers.clear()
    out = capfd.readouterr()
    assert "[ping address]" in out.err  # the redaction did fire on the deliberate leak
    for text in (out.out, out.err, (tmp_path / gonedark.STATE_FILE).read_text(), repr(config)):
        assert CANARY not in text
    assert len(svc.requests) == 3


def test_the_real_process_never_prints_the_address(tmp_path: Path) -> None:
    """``python -m hive.gonedark`` end to end: start, tick (skipped: no desk), stop."""
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("HIVE_")},
        "HIVE_GONEDARK_URL": f"https://127.0.0.1:9/{CANARY}",
        "HIVE_GONEDARK_STATE_DIR": str(tmp_path),
        "HIVE_GATEWAY_PORT": "9",
        "HIVE_GATEWAY_FM_HOME": str(tmp_path / "fm"),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "hive.gonedark"],
        env=env,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            if (tmp_path / gonedark.STATE_FILE).exists():
                break
            time.sleep(0.05)
    finally:
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, err
    assert "skipped the ping: the desk is not answering" in err
    assert "gone-dark sender stopped" in err
    for text in (out, err, (tmp_path / gonedark.STATE_FILE).read_text()):
        assert CANARY not in text
