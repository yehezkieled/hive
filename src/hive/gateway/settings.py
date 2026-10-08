"""Gateway configuration, read from the environment (no Hive core imports)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_OWNER = "yehezkieled1502@gmail.com"
DEFAULT_FM_HOME = "/home/hezki/firstmate"
DEFAULT_HOSTS = ("desktop-lfme032.tailfb3900.ts.net", "localhost", "127.0.0.1")
DEFAULT_DATA_DIR = "~/.local/state/hive-gateway"
DEFAULT_PORT = 8480
DEFAULT_ORIGIN = "https://desktop-lfme032.tailfb3900.ts.net:8446"
DEFAULT_BOARD_URL = "https://desktop-lfme032.tailfb3900.ts.net:8445"
DEFAULT_TZ = "Australia/Sydney"
LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1"})


def _csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(p.strip().lower() for p in value.split(",") if p.strip())


@dataclass(frozen=True)
class GatewaySettings:
    owner_login: str = DEFAULT_OWNER
    allowed_hosts: tuple[str, ...] = DEFAULT_HOSTS
    fm_home: Path = Path(DEFAULT_FM_HOME)
    # Only a request whose TCP peer is one of these may carry the login header.
    trusted_peers: frozenset[str] = field(default=LOOPBACK_PEERS)
    # Public origin of the Lavish board bridge; loopback board links are rewritten to it.
    board_url: str = DEFAULT_BOARD_URL
    # Time zone for server-rendered times; the page script carries its own fallback.
    default_tz: str = DEFAULT_TZ
    snapshot_ttl_s: float = 5.0
    snapshot_timeout_s: float = 20.0
    # Public tailnet origin the desk is served at (``tailscale serve``, never Funnel).
    origin: str = DEFAULT_ORIGIN
    # Local, outside the repo: VAPID keys and push subscriptions (files are mode 0600).
    data_dir: Path = Path(DEFAULT_DATA_DIR).expanduser()
    # Live updates: how often the hub looks at firstmate's state, and the live-tail bounds.
    poll_interval_s: float = 2.0
    receipts_interval_s: float = 5.0
    tail_lines: int = 60
    tail_timeout_s: float = 8.0
    # Run the change watcher and push (off in tests that do not start a server).
    live: bool = True
    # The quota chip's source: a ``quota-axi`` binary, or None for "quota unknown".
    quota_axi: Path | None = None
    quota_ttl_s: float = 60.0
    quota_timeout_s: float = 15.0
    quota_first_wait_s: float = 1.0
    # Claude Code's rate-limits cache file, the chip's headline source; None: quota-axi only.
    rate_limits: Path | None = None
    # Lavish's session state, read-only, for the Review pages list; None leaves the list out.
    lavish_state: Path | None = None
    # The ``lavish-axi`` binary that ends a review session; None hides the Close buttons.
    lavish_axi: Path | None = None

    @property
    def state_dir(self) -> Path:
        return self.fm_home / "state"

    @property
    def snapshot_script(self) -> Path:
        return self.fm_home / "bin" / "fm-fleet-snapshot.sh"

    @classmethod
    def from_env(cls) -> GatewaySettings:
        env = os.environ
        origin = env.get("HIVE_GATEWAY_ORIGIN", DEFAULT_ORIGIN).strip().rstrip("/")
        origin_host = (urlsplit(origin).hostname or "").lower()
        hosts = (origin_host, "localhost", "127.0.0.1") if origin_host else DEFAULT_HOSTS
        return cls(
            origin=origin,
            data_dir=Path(env.get("HIVE_GATEWAY_DATA_DIR", DEFAULT_DATA_DIR)).expanduser(),
            owner_login=env.get("HIVE_GATEWAY_OWNER", DEFAULT_OWNER).strip().lower(),
            allowed_hosts=_csv(env.get("HIVE_GATEWAY_HOSTS"), hosts),
            fm_home=Path(env.get("HIVE_GATEWAY_FM_HOME", DEFAULT_FM_HOME)),
            board_url=env.get("HIVE_GATEWAY_BOARD_URL", DEFAULT_BOARD_URL).strip().rstrip("/"),
            default_tz=env.get("HIVE_GATEWAY_TZ", DEFAULT_TZ).strip() or DEFAULT_TZ,
            quota_axi=_quota_axi(env.get("HIVE_GATEWAY_QUOTA_AXI", "").strip()),
            rate_limits=_rate_limits(env.get("HIVE_GATEWAY_RATE_LIMITS", "").strip()),
            lavish_state=_lavish_state(env.get("HIVE_GATEWAY_LAVISH_STATE", "").strip()),
            lavish_axi=_lavish_axi(env.get("HIVE_GATEWAY_LAVISH_AXI", "").strip()),
        )


def _lavish_state(value: str) -> Path | None:
    """The Lavish session file; ``off`` hides the Review pages list."""
    if value == "off":
        return None
    return Path(value or "~/.lavish-axi/state.json").expanduser()


def _lavish_axi(value: str) -> Path | None:
    """The configured binary, else ``lavish-axi`` on PATH or ``~/.local/bin``; ``off`` disables."""
    if value == "off":
        return None
    if value:
        return Path(value).expanduser()
    found = shutil.which("lavish-axi")
    if found:
        return Path(found)
    local = Path("~/.local/bin/lavish-axi").expanduser()
    return local if local.is_file() else None


def _rate_limits(value: str) -> Path | None:
    """Claude Code's rate-limits cache file; ``off`` leaves the chip on ``quota-axi``."""
    if value == "off":
        return None
    return Path(value or "~/.claude/rate-limits-cache.json").expanduser()


def _quota_axi(value: str) -> Path | None:
    """The configured binary, else ``quota-axi`` on PATH or in ``~/.local/bin``.

    A systemd user service often runs without ``~/.local/bin`` on its PATH, hence the
    fallback. ``off`` disables the chip's reads.
    """
    if value == "off":
        return None
    if value:
        return Path(value).expanduser()
    found = shutil.which("quota-axi")
    if found:
        return Path(found)
    local = Path("~/.local/bin/quota-axi").expanduser()
    return local if local.is_file() else None
