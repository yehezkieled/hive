"""Gateway configuration, read from the environment (no Hive core imports)."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
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
DEFAULT_FLEET_UP = Path(__file__).resolve().parents[3] / "scripts" / "fleet-up.sh"
LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1"})
# Under WSL the PC's own tools are Windows programs; a service's PATH does not list them.
WINDOWS_TAILSCALE = Path("/mnt/c/Program Files/Tailscale/tailscale.exe")
WINDOWS_POWERSHELL = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
WINDOWS_SCHTASKS = Path("/mnt/c/Windows/System32/schtasks.exe")
# The Windows admin helper's data folder (C:\ProgramData\HiveAdmin) as WSL sees it.
DEFAULT_ADMIN_HELPER = "/mnt/c/ProgramData/HiveAdmin"
DEFAULT_CLAUDE_CREDENTIALS = "~/.claude/.credentials.json"
DEFAULT_CLAUDE_REACH = "https://api.anthropic.com/v1/models"
_CLAUDE_REACH_RE = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]*)?")
DEFAULT_NET_CHECK = "api.anthropic.com:443"
DEFAULT_GONEDARK_ENV = "~/.config/hive/gonedark.env"
DEFAULT_GONEDARK_STATE = "~/.local/state/hive-gonedark"
_NET_CHECK_RE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?):([0-9]{1,5})$")


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

    # The idempotent fleet-up script whose firstmate step the wake control runs; None: off.
    fleet_up: Path | None = DEFAULT_FLEET_UP
    # herdr, for releasing a stale firstmate pane report on wake or restart; None: not found.
    herdr: Path | None = None

    # The Machine page's read-only sources. Each one left as None reads "unknown" there.
    # powershell.exe under WSL: Windows boot time, session and update facts.
    powershell: Path | None = None
    # The PC's Tailscale command (tailscale.exe under WSL): connection state and key expiry.
    tailscale: Path | None = None
    # systemctl, for the desk's own service and the fleet-up timer.
    systemctl: Path | None = None
    # Claude Code's credentials file. Only the sign-in's expiry date is ever taken from it.
    claude_credentials: Path | None = None
    # (host, port) of the one outbound reachability check.
    net_check: tuple[str, int] | None = None
    # The gone-dark alarm's private config (only whether it exists is read) and its
    # state dir (last tick's time and result). Either None: the Machine page leaves the
    # alarm line out.
    gonedark_env: Path | None = None
    gonedark_state: Path | None = None
    # How old the Machine page's readings may get before a page view starts a new round.
    machine_ttl_s: float = 15.0
    # The Terminal page: firstmate's own herdr pane in the browser, behind the step-up.
    terminal: bool = True

    # The Run page's Windows buttons: the admin helper's data folder (request file and audit
    # log) and schtasks.exe, which starts its task. Either None: no Windows buttons.
    admin_helper_root: Path | None = None
    schtasks: Path | None = None
    # The Sign-ins page's login commands. One left as None offers no Renew for that sign-in.
    claude: Path | None = None
    gh: Path | None = None
    # The Sign-ins page's live "can Claude answer?" light: the one address it asks, unsigned.
    # None: the light reads "not checked".
    claude_reach: str | None = None

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
            fleet_up=_fleet_up(env.get("HIVE_GATEWAY_FLEET_UP", "").strip()),
            herdr=_herdr(env.get("HIVE_GATEWAY_HERDR", "").strip()),
            powershell=_binary(
                env.get("HIVE_GATEWAY_POWERSHELL", "").strip(), "powershell.exe", WINDOWS_POWERSHELL
            ),
            tailscale=tailscale_binary(env),
            systemctl=_binary(env.get("HIVE_GATEWAY_SYSTEMCTL", "").strip(), "systemctl"),
            claude_credentials=_claude_credentials(
                env.get("HIVE_GATEWAY_CLAUDE_CREDENTIALS", "").strip()
            ),
            net_check=_net_check(env.get("HIVE_GATEWAY_NET_CHECK", "").strip()),
            gonedark_env=Path(
                env.get("HIVE_GONEDARK_ENV_FILE", "").strip() or DEFAULT_GONEDARK_ENV
            ).expanduser(),
            gonedark_state=Path(
                env.get("HIVE_GONEDARK_STATE_DIR", "").strip() or DEFAULT_GONEDARK_STATE
            ).expanduser(),
            admin_helper_root=_admin_helper(env.get("HIVE_GATEWAY_ADMIN_HELPER", "").strip()),
            schtasks=_binary(
                env.get("HIVE_GATEWAY_SCHTASKS", "").strip(), "schtasks.exe", WINDOWS_SCHTASKS
            ),
            claude=_binary(
                env.get("HIVE_GATEWAY_CLAUDE", "").strip(),
                "claude",
                Path("~/.local/bin/claude").expanduser(),
            ),
            gh=_binary(env.get("HIVE_GATEWAY_GH", "").strip(), "gh"),
            claude_reach=_claude_reach(env.get("HIVE_GATEWAY_CLAUDE_REACH", "").strip()),
            terminal=env.get("HIVE_GATEWAY_TERMINAL", "").strip().lower() != "off",
        )


def _binary(value: str, name: str, *fallbacks: Path) -> Path | None:
    """The configured binary, else ``name`` on PATH, else the first fallback that exists.

    ``off`` disables it.
    """
    if value == "off":
        return None
    if value:
        return Path(value).expanduser()
    found = shutil.which(name)
    if found:
        return Path(found)
    return next((p for p in fallbacks if p.is_file()), None)


def tailscale_binary(env: Mapping[str, str]) -> Path | None:
    """``HIVE_TAILSCALE_BIN``, else ``tailscale`` on PATH, else the Windows one under WSL.

    Shared by the desk's Machine page and the Telegram bot's ``/health``.
    """
    return _binary(env.get("HIVE_TAILSCALE_BIN", "").strip(), "tailscale", WINDOWS_TAILSCALE)


def _claude_credentials(value: str) -> Path | None:
    """Claude Code's credentials file; ``off`` leaves the sign-in row unknown."""
    if value == "off":
        return None
    return Path(value or DEFAULT_CLAUDE_CREDENTIALS).expanduser()


def _net_check(value: str) -> tuple[str, int] | None:
    """``host:port`` for the reachability check; ``off`` or an unusable value disables it."""
    if value == "off":
        return None
    m = _NET_CHECK_RE.fullmatch(value or DEFAULT_NET_CHECK)
    if m is None or not 0 < int(m[2]) < 65536:
        return None
    return m[1].lower(), int(m[2])


def _claude_reach(value: str) -> str | None:
    """The address the Claude light asks; ``off`` or anything but a plain https address
    disables it."""
    if value == "off":
        return None
    url = value or DEFAULT_CLAUDE_REACH
    return url if _CLAUDE_REACH_RE.fullmatch(url) else None


def _admin_helper(value: str) -> Path | None:
    """The admin helper's data folder; ``off`` removes the Run page's Windows buttons."""
    if value == "off":
        return None
    return Path(value or DEFAULT_ADMIN_HELPER).expanduser()


def _fleet_up(value: str) -> Path | None:
    """The fleet-up script; ``off`` removes the wake control's action."""
    if value == "off":
        return None
    return Path(value).expanduser() if value else DEFAULT_FLEET_UP


def _herdr(value: str) -> Path | None:
    """The configured binary, else ``herdr`` on PATH or in ``~/.local/bin``."""
    if value:
        return Path(value).expanduser()
    found = shutil.which("herdr")
    if found:
        return Path(found)
    local = Path("~/.local/bin/herdr").expanduser()
    return local if local.is_file() else None


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
