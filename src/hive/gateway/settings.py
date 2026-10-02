"""Gateway configuration, read from the environment (no Hive core imports)."""

from __future__ import annotations

import os
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
        )
