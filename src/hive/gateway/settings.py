"""Gateway configuration, read from the environment (no Hive core imports)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_OWNER = "yehezkieled1502@gmail.com"
DEFAULT_FM_HOME = "/home/hezki/firstmate"
DEFAULT_HOSTS = ("desktop-lfme032.tailfb3900.ts.net", "localhost", "127.0.0.1")
DEFAULT_PORT = 8480
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
    snapshot_ttl_s: float = 5.0
    snapshot_timeout_s: float = 20.0

    @property
    def snapshot_script(self) -> Path:
        return self.fm_home / "bin" / "fm-fleet-snapshot.sh"

    @classmethod
    def from_env(cls) -> GatewaySettings:
        env = os.environ
        return cls(
            owner_login=env.get("HIVE_GATEWAY_OWNER", DEFAULT_OWNER).strip().lower(),
            allowed_hosts=_csv(env.get("HIVE_GATEWAY_HOSTS"), DEFAULT_HOSTS),
            fm_home=Path(env.get("HIVE_GATEWAY_FM_HOME", DEFAULT_FM_HOME)),
        )
