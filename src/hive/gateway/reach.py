"""Can Claude's service answer right now? A live light for the Sign-ins page's Claude card.

A valid sign-in is not the same as a service that answers: Claude can turn every request
away (a Cloudflare block page, a 503) while the sign-in still has weeks left. This module
asks Claude's public API address one unsigned question and reads only how it answers.

The probe is one ``GET`` of ``https://api.anthropic.com/v1/models``, the host Claude Code
itself talks to (``HIVE_GATEWAY_CLAUDE_REACH`` changes it or turns it off), with no key, no
token, no cookie, no proxy or netrc credentials from the environment (``trust_env=False``)
and no redirect followed. With no key the API answers ``401``: that answer came from
Claude's own servers, past Cloudflare, so it reads as "answers". Nothing is billed: an
unsigned request is turned away before any model runs.

What it reads: the status code, the ``cf-mitigated`` header, and, for a ``403`` only, the
first few KiB of the body, searched for Cloudflare's block-page words and dropped. No body
is kept, logged or shown.

What it does NOT prove: that the owner's own signed-in requests will succeed. A green light
says Claude's front door answers this PC; the account behind the sign-in could still be
refused (an exhausted plan, a revoked sign-in). The sign-in file is never read here.

Rules this module keeps:

- At most one probe per ``MIN_GAP_S`` (60 s), however many pages ask; one at a time.
- It runs only when a Sign-ins page asks (a page view or the page's own 30 s refresh),
  never on a timer of its own, and never inside the request that asked: the page gets the
  last result at once.
- A short time-out (``TIMEOUT_S``, under 3 s), connect to last byte.
- Fails safe: any error, any answer it does not know, and any result older than
  ``FRESH_S`` reads grey, never a guessed green.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx

TIMEOUT_S = 2.5  # one probe, connect to last byte
MIN_GAP_S = 60.0  # between the starts of two probes, whatever the result
FRESH_S = 2 * MIN_GAP_S + TIMEOUT_S  # a result older than this reads grey
FIRST_WAIT_S = 0.5  # how long a page view may wait for a probe it just started
HEAD_BYTES = 8192  # of a 403's body, searched for the block-page words, then dropped
USER_AGENT = "hive-desk-reach"

# The light's three colours, in the Machine page's state words.
GREEN, RED, GREY = "ok", "down", "unknown"

# Cloudflare's own words on its block and challenge pages ("Attention Required! |
# Cloudflare", "Just a moment...") and its error page's element ids.
CLOUDFLARE_MARKS = (
    b"attention required! | cloudflare",
    b"just a moment...",
    b"cf-error-details",
    b"cf-chl-",
    b"challenge-platform",
)

# Answers that came from Claude's own servers: the API turning an unsigned request away.
ANSWERS = frozenset({200, 204, 400, 401, 404, 405})


@dataclass(frozen=True)
class Reach:
    """One probe's result, as the page shows it."""

    light: str  # GREEN, RED or GREY
    words: str  # a few plain words, e.g. "Claude's service is down (503)"
    at: float  # when the probe finished, on the checker's clock


@dataclass(frozen=True)
class Answer:
    """What the probe keeps of a response: the status and whether Cloudflare blocked it."""

    status: int
    cloudflare: bool = False


class ProbeError(Exception):
    """The probe could not get an answer. The message is one of the fixed phrases below."""


TIMED_OUT = "timed out"
NO_ROUTE = "no network or DNS"
NOT_ASKED = "the request failed"

Probe = Callable[[], Awaitable[Answer]]


def classify(answer: Answer, at: float) -> Reach:
    """The light for one answer. Anything not known here is grey."""
    code = answer.status
    if code == 403 and answer.cloudflare:
        return Reach(RED, "Claude's service refused the request (Cloudflare 403)", at)
    if code == 403:
        return Reach(RED, "Claude's service refused the request (403)", at)
    if code == 429:
        return Reach(RED, "Claude's service is turning requests away (429)", at)
    if code == 529:
        return Reach(RED, "Claude's service is overloaded (529)", at)
    if 500 <= code <= 599:
        return Reach(RED, f"Claude's service is down ({code})", at)
    if code in ANSWERS:
        return Reach(GREEN, "Claude's service answers", at)
    return Reach(GREY, f"Could not check: an answer not known here ({code})", at)


def failed(why: str, at: float) -> Reach:
    return Reach(GREY, f"Could not check ({why})", at)


def is_cloudflare_block(headers: Mapping[str, str], head: bytes) -> bool:
    """Cloudflare's challenge header, or its block page's words in the start of the body."""
    if headers.get("cf-mitigated", "").strip():
        return True
    low = head.lower()
    return any(mark in low for mark in CLOUDFLARE_MARKS)


def http_probe(
    url: str,
    *,
    timeout_s: float = TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Probe:
    """One unsigned GET of ``url``; keeps the status and the Cloudflare marker only."""

    async def ask() -> Answer:
        try:
            async with httpx.AsyncClient(
                timeout=timeout_s,
                trust_env=False,  # no proxy, netrc or SSL_CERT_* from the environment
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
                transport=transport,
            ) as client:
                return await asyncio.wait_for(_read(client, url), timeout_s)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProbeError(TIMED_OUT) from exc
        except (httpx.ConnectError, OSError) as exc:
            raise ProbeError(NO_ROUTE) from exc
        except (httpx.HTTPError, ssl.SSLError, ValueError) as exc:
            raise ProbeError(NOT_ASKED) from exc

    return ask


async def _read(client: httpx.AsyncClient, url: str) -> Answer:
    async with client.stream("GET", url) as response:
        head = b""
        if response.status_code == 403:  # only a refusal is searched for Cloudflare's words
            async for chunk in response.aiter_bytes():
                head += chunk
                if len(head) >= HEAD_BYTES:
                    break
        blocked = is_cloudflare_block(response.headers, head[:HEAD_BYTES])
        return Answer(response.status_code, blocked)


class ReachCheck:
    """The cached light, and the one probe that may run."""

    def __init__(
        self,
        probe: Probe | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        min_gap_s: float = MIN_GAP_S,
        fresh_s: float = FRESH_S,
    ) -> None:
        self._probe = probe
        self._clock = clock
        self._min_gap_s = min_gap_s
        self._fresh_s = fresh_s
        self._last: Reach | None = None
        self._started: float | None = None  # when the latest probe started
        self._task: asyncio.Task[Reach] | None = None
        self.probes = 0  # how many probes ran: for tests and nothing else

    @property
    def enabled(self) -> bool:
        return self._probe is not None

    async def _run(self) -> Reach:
        try:
            assert self._probe is not None
            answer = await self._probe()
            result = classify(answer, self._clock())
        except ProbeError as exc:
            result = failed(str(exc), self._clock())
        except Exception:  # never a guessed green: an error of any kind is grey
            result = failed(NOT_ASKED, self._clock())
        self._last = result
        return result

    def _kick(self) -> None:
        """Start a probe if none runs and the last one started at least ``min_gap_s`` ago."""
        if self._probe is None or (self._task is not None and not self._task.done()):
            return
        now = self._clock()
        if self._started is not None and now - self._started < self._min_gap_s:
            return
        with contextlib.suppress(RuntimeError):  # no running loop: caller is not async
            self._task = asyncio.get_running_loop().create_task(self._run())
            self._started = now
            self.probes += 1

    def peek(self) -> Reach | None:
        """The last result while it is fresh, else None; may start a probe behind the answer."""
        self._kick()
        last = self._last
        if last is None or self._clock() - last.at > self._fresh_s:
            return None
        return last

    async def light(self, wait_s: float = FIRST_WAIT_S) -> Reach | None:
        """``peek``, waiting at most ``wait_s`` for a probe that is running when nothing fresh
        is known yet."""
        last = self.peek()
        task = self._task
        if last is None and task is not None and not task.done() and wait_s > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), wait_s)
            last = self.peek()
        return last

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._task
