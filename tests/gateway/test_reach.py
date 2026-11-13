"""The Sign-ins page's live "can Claude answer right now?" light.

Every answer here comes from a fake HTTP layer (``httpx.MockTransport``) or a fake probe,
and every wait from a fake clock. No test makes a real network call.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import os
import time
from pathlib import Path

import httpx
import pytest

from hive.gateway import pages, reach, signins
from hive.gateway.app import create_app
from hive.gateway.machine import OK
from hive.gateway.reach import GREEN, GREY, RED, Answer, ReachCheck
from hive.gateway.settings import DEFAULT_CLAUDE_REACH, GatewaySettings
from hive.gateway.signins import SignInService
from tests.gateway.test_actions import CSRF, GOOD, HOST, OWNER, home  # noqa: F401
from tests.gateway.test_machine import DAY, NOW, _client, _main, credentials

URL = DEFAULT_CLAUDE_REACH
CANARY = "CANARYsecretCANARY"
CLOUDFLARE_PAGE = (
    b"<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title></head>"
    b"<body><div id='cf-error-details'>Sorry, you have been blocked</div></body></html>"
)


class Clock:
    def __init__(self, at: float = 1000.0) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


def transport(
    status: int, body: bytes = b"", headers: dict | None = None, seen: list | None = None
):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=body, headers=headers or {})

    return httpx.MockTransport(handle)


def raising(exc: Exception) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handle)


async def probe_once(t: httpx.AsyncBaseTransport, timeout_s: float = reach.TIMEOUT_S):
    """One probe through the real HTTP code over a fake transport, as the page's light."""
    check = ReachCheck(reach.http_probe(URL, timeout_s=timeout_s, transport=t))
    result = await check.light(wait_s=5)
    await check.stop()
    assert result is not None
    return result


# ---- what each answer means ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "cloudflare", "light", "words"),
    [
        (200, False, GREEN, "Claude's service answers"),
        (401, False, GREEN, "Claude's service answers"),  # the unsigned request's usual answer
        (404, False, GREEN, "Claude's service answers"),
        (403, True, RED, "Claude's service refused the request (Cloudflare 403)"),
        (403, False, RED, "Claude's service refused the request (403)"),
        (429, False, RED, "Claude's service is turning requests away (429)"),
        (500, False, RED, "Claude's service is down (500)"),
        (503, False, RED, "Claude's service is down (503)"),
        (529, False, RED, "Claude's service is overloaded (529)"),
        (302, False, GREY, "Could not check: an answer not known here (302)"),
        (418, False, GREY, "Could not check: an answer not known here (418)"),
    ],
)
def test_each_answer_reads_as_one_light(
    status: int, cloudflare: bool, light: str, words: str
) -> None:
    got = reach.classify(Answer(status, cloudflare), 5.0)
    assert (got.light, got.words, got.at) == (light, words, 5.0)


@pytest.mark.parametrize(
    ("status", "body", "headers", "light", "words"),
    [
        (401, b'{"type":"error"}', {}, GREEN, "Claude's service answers"),
        (200, b"{}", {}, GREEN, "Claude's service answers"),
        (404, b"", {}, GREEN, "Claude's service answers"),
        (403, CLOUDFLARE_PAGE, {}, RED, "Claude's service refused the request (Cloudflare 403)"),
        (403, b"<html>Just a moment...</html>", {}, RED, "(Cloudflare 403)"),
        (403, b"", {"cf-mitigated": "challenge"}, RED, "(Cloudflare 403)"),
        (403, b'{"error":"forbidden"}', {}, RED, "Claude's service refused the request (403)"),
        (503, b"upstream connect error", {}, RED, "Claude's service is down (503)"),
        (502, CLOUDFLARE_PAGE, {}, RED, "Claude's service is down (502)"),
    ],
)
async def test_the_probe_reads_the_status_and_the_cloudflare_marker(
    status: int, body: bytes, headers: dict, light: str, words: str
) -> None:
    got = await probe_once(transport(status, body, headers))
    assert got.light == light and words in got.words


async def test_only_the_start_of_a_refusal_is_searched_for_cloudflare() -> None:
    late = b"x" * (reach.HEAD_BYTES + 10) + CLOUDFLARE_PAGE
    got = await probe_once(transport(403, late))
    assert got.words == "Claude's service refused the request (403)"


@pytest.mark.parametrize(
    ("exc", "why"),
    [
        (httpx.ConnectError("[Errno -2] Name or service not known"), "no network or DNS"),
        (httpx.ConnectTimeout("connect timed out"), "timed out"),
        (httpx.ReadTimeout("read timed out"), "timed out"),
        (httpx.RemoteProtocolError("bad"), "the request failed"),
        (OSError("network unreachable"), "no network or DNS"),
    ],
)
async def test_an_error_reads_grey_never_green(exc: Exception, why: str) -> None:
    got = await probe_once(raising(exc))
    assert (got.light, got.words) == (GREY, f"Could not check ({why})")


async def test_a_service_that_never_answers_is_cut_off_at_the_time_out() -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    start = time.monotonic()
    got = await probe_once(httpx.MockTransport(hang), timeout_s=0.2)
    assert time.monotonic() - start < 1.5
    assert (got.light, got.words) == (GREY, "Could not check (timed out)")
    assert reach.TIMEOUT_S < 3


async def test_a_probe_that_breaks_in_any_way_reads_grey() -> None:
    async def broken() -> Answer:
        raise KeyError("surprise")

    check = ReachCheck(broken)
    got = await check.light(wait_s=1)
    assert got is not None and got.light == GREY


async def test_the_request_is_one_bare_get_with_no_credential() -> None:
    seen: list[httpx.Request] = []
    await probe_once(transport(401, seen=seen))
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET" and str(request.url) == URL
    names = {k.lower() for k in request.headers}
    assert not names & {"authorization", "x-api-key", "cookie", "proxy-authorization"}
    assert request.headers["user-agent"] == reach.USER_AGENT
    assert request.content == b""


async def test_a_redirect_is_not_followed() -> None:
    seen: list[httpx.Request] = []
    got = await probe_once(transport(302, headers={"location": "https://example.test/"}, seen=seen))
    assert len(seen) == 1 and got.light == GREY


# ---- the rate limit and the cache ---------------------------------------------------------


class FakeProbe:
    def __init__(self, *answers: Answer | Exception) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def __call__(self) -> Answer:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        a = self.answers[min(self.calls, len(self.answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return a


async def test_many_viewers_start_at_most_one_probe_a_minute() -> None:
    clock, probe = Clock(), FakeProbe(Answer(401), Answer(503))
    check = ReachCheck(probe, clock=clock)
    first = await asyncio.gather(*(check.light(wait_s=1) for _ in range(20)))
    assert probe.calls == 1 and all(r is not None and r.light == GREEN for r in first)
    for _ in range(30):  # every refresh for the next 59 s gets the cached answer
        clock.at += 59 / 30
        got = await check.light()
        assert got is not None and got.light == GREEN
    await asyncio.sleep(0)
    assert probe.calls == 1
    clock.at += 1.5  # a minute after the first probe started: the next ask starts one
    check.peek()
    await asyncio.sleep(0.01)
    assert probe.calls == 2
    got = check.peek()
    assert got is not None and got.words == "Claude's service is down (503)"


async def test_a_failed_probe_is_rate_limited_too() -> None:
    clock = Clock()
    probe = FakeProbe(reach.ProbeError(reach.TIMED_OUT))
    check = ReachCheck(probe, clock=clock)
    for _ in range(10):
        await check.light(wait_s=1)
        clock.at += 5
    assert probe.calls == 1


async def test_only_one_probe_runs_at_a_time() -> None:
    clock, probe = Clock(), FakeProbe(Answer(401))
    probe.gate = asyncio.Event()
    check = ReachCheck(probe, clock=clock)
    assert await check.light(wait_s=0.05) is None  # still running: nothing known yet
    clock.at += 300  # long past the gap, but the first one is still running
    assert check.peek() is None
    assert probe.calls == 1
    probe.gate.set()
    await asyncio.sleep(0.01)
    assert check.peek() is not None


async def test_a_page_view_never_waits_long_for_the_probe() -> None:
    probe = FakeProbe(Answer(401))
    probe.gate = asyncio.Event()  # a probe that hangs
    check = ReachCheck(probe)
    start = time.monotonic()
    assert await check.light(wait_s=0.1) is None
    assert time.monotonic() - start < 0.5
    await check.stop()


async def test_an_old_result_reads_grey_not_green() -> None:
    clock, probe = Clock(), FakeProbe(Answer(401))
    check = ReachCheck(probe, clock=clock, min_gap_s=60, fresh_s=122.5)
    assert (await check.light(wait_s=1)).light == GREEN
    probe.gate = asyncio.Event()  # the next probe hangs
    clock.at += 123
    assert check.peek() is None  # too old to show, and the new one has not answered
    await asyncio.sleep(0)
    assert probe.calls == 2
    await check.stop()


async def test_no_probe_runs_unless_a_page_asks() -> None:
    probe = FakeProbe(Answer(401))
    check = ReachCheck(probe)
    await asyncio.sleep(0.05)
    assert probe.calls == 0 and check.probes == 0


async def test_turned_off_nothing_is_asked() -> None:
    check = ReachCheck(None)
    assert not check.enabled and await check.light() is None and check.probes == 0


# ---- the light beside the sign-in ---------------------------------------------------------


def _result(light: str, words: str) -> reach.Reach:
    return reach.Reach(light, words, 0.0)


def test_a_valid_sign_in_beside_a_red_light_says_the_sign_in_is_fine() -> None:
    red = _result(RED, "Claude's service refused the request (Cloudflare 403)")
    got = signins.reach_light(red, True, OK)
    assert got.words == (
        "Claude's service refused the request (Cloudflare 403) — your sign-in is fine"
    )
    assert (got.light, got.chip) == (RED, "Not answering")
    signed_out = signins.reach_light(red, True, "down")
    assert signed_out.words == "Claude's service refused the request (Cloudflare 403)"
    green = signins.reach_light(_result(GREEN, "Claude's service answers"), True, OK)
    assert (green.light, green.chip, green.words) == (GREEN, "Answers", "Claude's service answers")
    assert signins.reach_light(None, True, OK) == signins.Light(
        GREY, "Unknown", "Checking Claude's service…"
    )
    assert signins.reach_light(None, False, OK).light == GREY


def test_the_signed_out_state_stays_separate_from_the_light(tmp_path: Path) -> None:
    settings = GatewaySettings(data_dir=tmp_path, claude=Path("/bin/true"))
    states = signins.States(
        signins.Reading(NOW - DAY, NOW),
        signins.Reading(signins.GitHub("success", False), NOW),
        signins.Reading(signins.Unknown("off"), NOW),
        NOW,
    )
    service = signins.Service(_result(GREEN, "Claude's service answers"), True)
    claude, github, tailscale = signins.build_rows(states, NOW, settings, service)
    assert claude.state == "down" and claude.renewable  # signed out: renew, as before
    assert claude.reach is not None and claude.reach.light == GREEN
    assert github.reach is None and tailscale.reach is None


def test_the_card_shows_a_chip_the_words_and_what_the_check_cannot_prove(tmp_path: Path) -> None:
    settings = GatewaySettings(data_dir=tmp_path, claude=Path("/bin/true"))
    states = signins.States(
        signins.Reading(NOW + 10 * DAY, NOW),
        signins.Reading(signins.GitHub("success", False), NOW),
        signins.Reading(signins.Unknown("off"), NOW),
        NOW,
    )
    red = _result(RED, "Claude's service is down (503)")
    rows = signins.build_rows(states, NOW, settings, signins.Service(red, True))
    panel = signins.Panel(rows, signins.FlowView(), NOW)
    html = pages.render_signins(panel, pages.Ctx(CSRF.csrf(), nxt="/signins"))
    assert html.count("<details class='sgreach sgreach--down'>") == 1
    assert "<span class='sc sc--down'><i></i>Not answering</span>" in html
    assert "Claude&#x27;s service is down (503) — your sign-in is fine" in html
    assert "cannot prove your own account&#x27;s requests will succeed" in html
    assert "title='Asks Claude&#x27;s public API address one unsigned question" in html


# ---- no credential is ever read, sent, logged or shown ------------------------------------


async def test_no_credential_is_ever_read_sent_logged_or_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    creds = tmp_path / "claude.credentials"
    creds.write_text(credentials().replace("}", f',"canary":"{CANARY}"}}', 1))
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.setenv(name, CANARY)
    monkeypatch.setenv("HTTPS_PROXY", f"http://user:{CANARY}@proxy.invalid:3128")
    netrc = tmp_path / "netrc"
    netrc.write_text(f"machine api.anthropic.com login me password {CANARY}\n")
    monkeypatch.setenv("NETRC", str(netrc))

    opened: list[str] = []
    real_open, real_os_open = builtins.open, os.open

    def spy_open(file, *a, **k):  # noqa: ANN001, ANN002, ANN003
        opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_os_open(path, *a, **k):  # noqa: ANN001, ANN002, ANN003
        opened.append(str(path))
        return real_os_open(path, *a, **k)

    seen: list[httpx.Request] = []
    settings = GatewaySettings(data_dir=tmp_path / "data", claude_credentials=creds)
    check = ReachCheck(reach.http_probe(URL, transport=transport(401, seen=seen)))
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    try:
        got = await check.light(wait_s=5)
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
        monkeypatch.setattr(os, "open", real_os_open)
    assert got is not None and got.light == GREEN
    assert str(creds) not in opened and str(netrc) not in opened
    (request,) = seen
    sent = str(request.url) + repr(list(request.headers.raw)) + request.content.decode()
    assert CANARY not in sent and "proxy.invalid" not in sent
    rows = signins.build_rows(None, NOW, settings, signins.Service(got, True))
    page = pages.render_signins(
        signins.Panel(rows, signins.FlowView(), None), pages.Ctx(CSRF.csrf(), nxt="/signins")
    )
    assert CANARY not in page and CANARY not in caplog.text and CANARY not in repr(got)
    await check.stop()


# ---- the page, through the desk -----------------------------------------------------------


def test_the_page_answers_at_once_while_the_probe_hangs(tmp_path: Path, home: Path) -> None:  # noqa: F811
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    settings = GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=home,
        data_dir=tmp_path / "data",
        live=False,
    )
    check = ReachCheck(reach.http_probe(URL, transport=httpx.MockTransport(hang)))
    service = SignInService(settings, reach_check=check)
    with _client(create_app(settings, tokens=CSRF, signin=service)) as c:
        for _ in range(3):
            start = time.monotonic()
            res = c.get("/signins", headers=GOOD)
            assert time.monotonic() - start < 1.0
            assert res.status_code == 200
            main = _main(res.text)
            assert "Checking Claude&#x27;s service…" in main
            assert "<details class='sgreach sgreach--unknown'>" in main
    assert check.probes == 1


def test_settings_turn_the_light_on_by_default_and_off_by_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HIVE_GATEWAY_CLAUDE_REACH", raising=False)
    assert GatewaySettings.from_env().claude_reach == "https://api.anthropic.com/v1/models"
    monkeypatch.setenv("HIVE_GATEWAY_CLAUDE_REACH", "https://example.test/ping")
    assert GatewaySettings.from_env().claude_reach == "https://example.test/ping"
    for bad in ("off", "http://example.test/", "https://user:pw@example.test/", "a b"):
        monkeypatch.setenv("HIVE_GATEWAY_CLAUDE_REACH", bad)
        assert GatewaySettings.from_env().claude_reach is None, bad
    assert GatewaySettings().claude_reach is None  # a hand-built settings object asks nothing
