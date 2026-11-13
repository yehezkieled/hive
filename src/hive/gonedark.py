"""The "gone dark" alarm: one outbound ping that says the PC is still up.

``python -m hive.gonedark`` (run by ``scripts/hive-gonedark.sh`` under
``hive-gonedark.service``). Every ``interval`` (default 5 minutes) it checks, cheaply
and locally, that the desk answers on loopback, that a firstmate session runs and that
Tailscale is signed in and connected. Only then does it make ONE HTTPS GET to the ping
address the owner configured, at a hosted dead-man's-switch service that alerts their
phone when the pings stop. When the check fails it skips the ping, so the missing ping
is the alarm, and logs why.

Rules:

- The request is a bare GET: no body, no query added, no cookies, no proxy or netrc
  credentials (``trust_env=False``), a fixed ``User-Agent``, no redirects followed. It
  carries no PC name, token or machine data beyond what the address itself holds.
- One request per tick, under ``REQUEST_TIMEOUT_S``. A failure waits for the next tick:
  there is no retry and no catch-up burst after a long pause.
- The address is a secret. It never reaches a log line, the state file or any output
  (``RedactingFormatter``, ``GoneDarkConfig.__repr__``); httpx's request logging is off.
- Off unless configured: no address, no process. One instance per state dir (a lock).
- No listener: the process opens no port.

The state file (``last.json`` in the state dir) holds only the last tick's time, its
result (``sent``, ``skipped`` or ``failed``), a fixed reason phrase and the interval. The
desk's Machine page reads it for its one "gone-dark alarm" line.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import re
import signal
import ssl
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import httpx

from hive import standalone
from hive.gateway.settings import (
    DEFAULT_FM_HOME,
    DEFAULT_GONEDARK_STATE,
    DEFAULT_PORT,
    tailscale_binary,
)
from hive.gateway.wake import desk_probe, read_status
from hive.standalone import EX_CONFIG, tailscale_state

logger = logging.getLogger("hive.gonedark")

DEFAULT_INTERVAL_S = 300
MIN_INTERVAL_S = 60
MAX_INTERVAL_S = 3600
REQUEST_TIMEOUT_S = 8.0  # the whole request, connect to last byte; under 10 s
DEFAULT_STATE_DIR = DEFAULT_GONEDARK_STATE
STATE_FILE = "last.json"
LOCK_FILE = "sender.lock"
USER_AGENT = "hive-gonedark"
MAX_URL_CHARS = 2048
# https only; a host name or address, an optional port, then a path made of URL-safe
# characters. No user:password@ part, no spaces, no quotes, no shell characters. The
# same pattern is in scripts/hive-gonedark.sh and scripts/gonedark-setup.sh.
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%!&+,;=:@/?#-]*)?$")

SENT, SKIPPED, FAILED = "sent", "skipped", "failed"
# Every reason the state file and the log can carry. Fixed phrases, never raw output.
DESK_DOWN = "the desk is not answering"
NO_FIRSTMATE = "no firstmate session is running"
TAILSCALE_DOWN = "Tailscale is signed out or not connected"
CHECK_FAILED = "the health check could not run"
TIMED_OUT = "the ping timed out"
NOT_SENT = "the ping could not be sent"
REDIRECTED = "the ping service answered with a redirect"
REFUSED = "the ping service answered with an error"
REASONS = frozenset({"", DESK_DOWN, NO_FIRSTMATE, CHECK_FAILED, TIMED_OUT, NOT_SENT})
REASONS |= {REDIRECTED, REFUSED, TAILSCALE_DOWN}


class GoneDarkConfigError(ValueError):
    """The alarm's config is missing or malformed. The message never holds a value."""


@dataclass(frozen=True)
class GoneDarkConfig:
    url: str
    interval_s: int

    def __repr__(self) -> str:  # never print the address, even by accident
        return f"GoneDarkConfig(url=<hidden>, interval_s={self.interval_s})"


def valid_url(url: str) -> bool:
    return len(url) <= MAX_URL_CHARS and URL_RE.match(url) is not None


def load_config(env: Mapping[str, str]) -> GoneDarkConfig:
    """Read and check ``HIVE_GONEDARK_URL`` and ``HIVE_GONEDARK_INTERVAL_S``."""
    url = env.get("HIVE_GONEDARK_URL", "").strip()
    if not url:
        raise GoneDarkConfigError("HIVE_GONEDARK_URL is not set")
    if not valid_url(url):
        raise GoneDarkConfigError("HIVE_GONEDARK_URL must be a plain https:// address")
    raw = env.get("HIVE_GONEDARK_INTERVAL_S", "").strip() or str(DEFAULT_INTERVAL_S)
    if not raw.isdigit() or not MIN_INTERVAL_S <= int(raw) <= MAX_INTERVAL_S:
        raise GoneDarkConfigError(
            f"HIVE_GONEDARK_INTERVAL_S must be whole seconds, {MIN_INTERVAL_S} to {MAX_INTERVAL_S}"
        )
    return GoneDarkConfig(url, int(raw))


class RedactingFormatter(standalone.RedactingFormatter):
    label = "[ping address]"


def setup_logging(url: str = "") -> None:
    standalone.setup_logging(RedactingFormatter(standalone.LOG_FORMAT, (url,)))


# ---- the two halves of a tick ----------------------------------------------------------

# None: healthy; otherwise one of the fixed reasons above.
Health = Callable[[], Awaitable[str | None]]
# None: the ping was accepted; otherwise one of the fixed reasons above.
Ping = Callable[[], Awaitable[str | None]]


def local_health(port: int, fm_home: Path, tailscale: Path | None) -> Health:
    """The desk answers on loopback, a firstmate session runs and Tailscale is connected.

    A Tailscale answer that cannot be read ("unknown ...") counts as healthy, so a probe
    glitch never raises a false alarm.
    """

    async def check() -> str | None:
        if not await desk_probe(port):
            return DESK_DOWN
        try:
            status = await asyncio.to_thread(read_status, fm_home)
        except Exception:  # an unreadable /proc is not a healthy PC
            return CHECK_FAILED
        if not status.session:
            return NO_FIRSTMATE
        state = await tailscale_state(tailscale)
        if state == "connected" or state.startswith("unknown"):
            return None
        return TAILSCALE_DOWN

    return check


def https_ping(
    url: str, timeout_s: float = REQUEST_TIMEOUT_S, verify: ssl.SSLContext | bool = True
) -> Ping:
    """One bare GET to ``url``. Any 2xx is accepted; anything else is a fixed reason."""

    async def ping() -> str | None:
        try:
            async with httpx.AsyncClient(
                timeout=timeout_s,
                trust_env=False,  # no proxy, netrc or SSL_CERT_* from the environment
                follow_redirects=False,
                verify=verify,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await asyncio.wait_for(client.get(url), timeout_s)
        except (TimeoutError, httpx.TimeoutException):
            return TIMED_OUT
        except (httpx.HTTPError, OSError, ssl.SSLError):
            return NOT_SENT
        if 200 <= response.status_code < 300:
            return None
        if 300 <= response.status_code < 400:
            return REDIRECTED
        return REFUSED

    return ping


# ---- the sender ------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    at: float
    result: str  # SENT, SKIPPED or FAILED
    reason: str = ""  # one of REASONS; empty when sent


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class Sender:
    """Ticks every ``interval_s`` (monotonic), never in a burst, until ``stop`` is set."""

    def __init__(
        self,
        health: Health,
        ping: Ping,
        state_dir: Path,
        interval_s: float,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._health = health
        self._ping = ping
        self._state = state_dir / STATE_FILE
        self.interval_s = interval_s
        self._clock = clock
        self._monotonic = monotonic
        self._last_result = ""

    async def tick(self) -> Outcome:
        try:
            reason = await self._health()
        except Exception:
            reason = CHECK_FAILED
        if reason is not None:
            outcome = Outcome(self._clock(), SKIPPED, reason)
            logger.warning("skipped the ping: %s", reason)
        else:
            try:
                reason = await self._ping()
            except Exception:
                reason = NOT_SENT
            if reason is None:
                outcome = Outcome(self._clock(), SENT)
                if self._last_result != SENT:  # one line on recovery, not one per tick
                    logger.info("ping sent; the next one in %ds", self.interval_s)
            else:
                outcome = Outcome(self._clock(), FAILED, reason)
                logger.warning("ping failed: %s; trying again at the next tick", reason)
        self._last_result = outcome.result
        self._record(outcome)
        return outcome

    def _record(self, outcome: Outcome) -> None:
        body = {
            "at": outcome.at,
            "result": outcome.result,
            "reason": outcome.reason,
            "interval_s": self.interval_s,
        }
        try:
            _write_private(self._state, json.dumps(body) + "\n")
        except OSError:
            logger.warning("could not write the gone-dark state file")

    async def run(
        self, stop: asyncio.Event, wait: Callable[[float], Awaitable[None]] | None = None
    ) -> None:
        """Tick now, then on the interval. A late tick moves the schedule; it never catches up."""

        async def until_stop(seconds: float) -> None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), seconds)

        wait = wait or until_stop
        due = self._monotonic()
        while not stop.is_set():
            await self.tick()
            due += self.interval_s
            now = self._monotonic()
            if due <= now:  # the tick overran its slot: start counting from now
                due = now + self.interval_s
            if stop.is_set():
                break
            await wait(due - now)


def read_last(state_dir: Path) -> Outcome | None:
    """The last tick from the state file; None when absent or not understood."""
    try:
        data = json.loads((state_dir / STATE_FILE).read_text()[:4096])
        at, result, reason = float(data["at"]), data["result"], data.get("reason", "")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if result not in (SENT, SKIPPED, FAILED) or reason not in REASONS:
        return None
    return Outcome(at, result, reason)


def read_interval(state_dir: Path) -> int:
    """The interval the sender recorded, else the default."""
    try:
        value = int(json.loads((state_dir / STATE_FILE).read_text()[:4096])["interval_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_INTERVAL_S
    return value if MIN_INTERVAL_S <= value <= MAX_INTERVAL_S else DEFAULT_INTERVAL_S


def take_lock(state_dir: Path) -> IO[str] | None:
    """Hold the single-instance lock for this process's life; None when another holds it."""
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    f = open(state_dir / LOCK_FILE, "a")  # noqa: SIM115 - held open for the process's life
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


# ---- entry point -----------------------------------------------------------------------


def main(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    setup_logging()
    try:
        config = load_config(env)
    except GoneDarkConfigError as exc:
        logger.error("not starting: %s (see docs/gone-dark.md)", exc)
        return EX_CONFIG
    setup_logging(config.url)
    state_dir = Path(env.get("HIVE_GONEDARK_STATE_DIR", DEFAULT_STATE_DIR)).expanduser()
    lock = take_lock(state_dir)
    if lock is None:
        logger.info("another gone-dark sender already runs; not starting a second")
        return 0
    port = int(env.get("HIVE_GATEWAY_PORT", "") or DEFAULT_PORT)
    fm_home = Path(env.get("HIVE_GATEWAY_FM_HOME", "") or DEFAULT_FM_HOME)
    sender = Sender(
        local_health(port, fm_home, tailscale_binary(env)),
        https_ping(config.url),
        state_dir,
        config.interval_s,
    )

    async def _main() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        logger.info(
            "gone-dark alarm on: one ping every %ds while the desk, firstmate and Tailscale are up",
            config.interval_s,
        )
        await sender.run(stop)
        logger.info("gone-dark sender stopped")
        return 0

    try:
        return asyncio.run(_main())
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
