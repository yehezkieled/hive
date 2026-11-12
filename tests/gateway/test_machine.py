# ruff: noqa: F811
"""The Machine page: probes parsed from sample output, time-outs, redaction, the rules
behind each row, and the page itself.

Every probe source is a fake script or a temp file. Nothing here runs PowerShell,
Tailscale or systemctl for real, reads a real credentials file or reaches off this host.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive import gonedark
from hive.gateway import machine, pages
from hive.gateway.app import _safe_next, create_app
from hive.gateway.live import LiveHub
from hive.gateway.settings import GatewaySettings, tailscale_binary
from hive.gateway.wake import FirstmateStatus, StatusCache
from hive.gateway.watchdog import View
from tests.gateway.test_actions import CSRF, GOOD, HOST, OWNER, home  # noqa: F401  (fixture)
from tests.gateway.test_stack_home import FAKE_LIVE, _in_chrome, needs_chrome

NOW = datetime(2026, 11, 11, 5, 0, tzinfo=UTC).timestamp()
DAY = 86400.0
SECRET = "sk-ant-oat01-PLANTEDsecretPLANTEDsecretPLANTED"
LOGIN = "someone@example.test"
AUTH_URL = "https://login.tailscale.example/a/PLANTEDauthPLANTED"

BOOT_OUT = "2026-10-21T11:07:42.5005400Z\r\n"
SESSION_OUT = '{"logonui":[1],"wu":false,"shell":[1],"cbs":false}\r\n'
UPDATES_OUT = '{"on":"2026-10-13"}\r\n'
CLAUDE_EXPIRY_MS = 1795240278775  # 2026-11-21T05:51:18Z


def tailscale_json(**changes: object) -> str:
    """``tailscale status --json`` as the PC prints it, with logins and a sign-in link."""
    data: dict = {
        "Version": "1.90.0",
        "BackendState": "Running",
        "AuthURL": AUTH_URL,
        "TailscaleIPs": ["100.64.0.1"],
        "Self": {
            "HostName": "DESKTOP-TEST",
            "DNSName": "desktop-test.tail0000.ts.net.",
            "UserID": 7,
            "Online": True,
            "KeyExpiry": "2027-04-20T11:58:33Z",
            "Expired": None,
        },
        "User": {"7": {"LoginName": LOGIN, "DisplayName": "Some One"}},
        "Peer": {
            "nodekey:a": {
                "HostName": "localhost",  # phones report this; the MagicDNS label is the name
                "DNSName": "ipad171.tail0000.ts.net.",
                "UserID": 7,
                "KeyExpiry": "2026-12-16T13:49:26Z",
            },
            "nodekey:b": {
                "HostName": "localhost",
                "DNSName": "iphone-13.tail0000.ts.net.",
                "UserID": 7,
                "KeyExpiry": "2026-09-24T23:03:47Z",
                "Expired": True,
            },
            "nodekey:c": {  # another account's device: never listed
                "HostName": "someone-elses",
                "DNSName": "other.tail0000.ts.net.",
                "UserID": 9,
                "KeyExpiry": "2026-11-30T00:00:00Z",
            },
            "nodekey:d": {  # Go's zero time: this key does not expire
                "HostName": "no-expiry-box",
                "DNSName": "",
                "UserID": 7,
                "KeyExpiry": "0001-01-01T00:00:00Z",
            },
        },
    }
    data.update(changes)
    return json.dumps(data)


def units_text(
    *,
    desk_pid: int = 4242,
    timer_load: str = "loaded",
    timer: str = "active",
    result: str = "success",
    status: int = 0,
    last: float | None = NOW - 120,
) -> str:
    """``systemctl --user show`` for the desk service and the fleet-up timer and service."""
    ran = f"@{int(last)}" if last else ""
    return (
        "Id=hive-gateway.service\nLoadState=loaded\nActiveState=active\nResult=success\n"
        f"MainPID={desk_pid}\nActiveEnterTimestamp=@{int(NOW - 7200)}\n"
        "ExecMainStatus=0\nExecMainExitTimestamp=\n\n"
        f"Id=hive-fleet-up.timer\nLoadState={timer_load}\nActiveState={timer}\nResult=success\n"
        f"LastTriggerUSec={ran}\n\n"
        "Id=hive-fleet-up.service\nLoadState=loaded\nActiveState=inactive\n"
        f"Result={result}\nMainPID=0\nExecMainStatus={status}\nExecMainExitTimestamp={ran}\n"
    )


def credentials(expires: object = CLAUDE_EXPIRY_MS) -> str:
    """Claude Code's credentials file: tokens everywhere, one expiry date."""
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": SECRET,
                "refreshToken": SECRET + "r",
                "expiresAt": 1794400000000,
                "refreshTokenExpiresAt": expires,
                "scopes": ["user:inference"],
            },
            "mcpOAuth": {"x": {"accessToken": SECRET + "m"}},
        }
    )


def _exe(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@dataclass
class Rig:
    settings: GatewaySettings
    calls: Path  # one line per fake PowerShell launch: boot, session or updates
    slow: Path  # while this file exists every fake command hangs
    uptime: Path

    def launched(self) -> list[str]:
        return self.calls.read_text().split() if self.calls.exists() else []


def rig(
    tmp_path: Path,
    *,
    tailscale: str | None = None,
    units: str | None = None,
    creds: str | None = None,
    net: tuple[str, int] | None = None,
    **settings: object,
) -> Rig:
    """Fake probe sources that print the sample output, wired into gateway settings."""
    root = tmp_path / "rig"
    root.mkdir()
    calls, slow = root / "calls", root / "slow"
    hang = f"[ -e '{slow}' ] && exec sleep 30"
    (root / "ts.json").write_text(tailscale_json() if tailscale is None else tailscale)
    (root / "units.txt").write_text(units_text() if units is None else units)
    (root / "credentials.json").write_text(credentials() if creds is None else creds)
    (root / "uptime").write_text("5000.25 123.00\n")
    powershell = _exe(
        root / "powershell.exe",
        f"{hang}\n"
        'case "$*" in\n'
        f"  *LastBootUpTime*) echo boot >> '{calls}'; printf '%s' '{BOOT_OUT}' ;;\n"
        f"  *LogonUI*) echo session >> '{calls}'; printf '%s' '{SESSION_OUT}' ;;\n"
        f"  *Get-HotFix*) echo updates >> '{calls}'; printf '%s' '{UPDATES_OUT}' ;;\n"
        "  *) exit 9 ;;\n"
        "esac",
    )
    made = GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=tmp_path,
        snapshot_ttl_s=60,
        data_dir=tmp_path / "data",
        live=False,
        powershell=powershell,
        tailscale=_exe(root / "tailscale", f"{hang}\ncat '{root / 'ts.json'}'"),
        systemctl=_exe(root / "systemctl", f"{hang}\ncat '{root / 'units.txt'}'"),
        claude_credentials=root / "credentials.json",
        net_check=net,
        **settings,  # type: ignore[arg-type]
    )
    return Rig(made, calls, slow, root / "uptime")


class Clock:
    def __init__(self, at: float = NOW) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


WATCHDOG_OFF = View(False, "", False, "", 0, ())


def monitor_for(
    r: Rig,
    *,
    state: str = "alive",
    watchdog: View = WATCHDOG_OFF,
    clock: Clock | None = None,
    on_change=None,
) -> machine.MachineMonitor:
    pids = () if state == "down" else (4242,)
    status = StatusCache(r.settings, reader=lambda _home: FirstmateStatus(state, pids, 12.0, NOW))
    return machine.MachineMonitor(
        r.settings, status, lambda: watchdog, on_change, clock or Clock(), r.uptime
    )


@pytest.fixture(autouse=True)
def no_real_claude_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Firstmate's last turn comes from Claude Code's own records; never read the real ones."""
    monkeypatch.setattr(machine, "last_activity", lambda _home, _pid: 300.0)


@contextmanager
def listening() -> Iterator[int]:
    """A loopback port that accepts connections: the reachability check's target."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(4)
        yield s.getsockname()[1]


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rows(view: machine.MachineView) -> dict[str, machine.Row]:
    return {row.key: row for row in view.rows}


def view_of(readings: machine.Readings, now: float = NOW, pid: int = 4242) -> machine.MachineView:
    return machine.build_view(readings, now, now - 4000, pid)


def reading(value: object, at: float = NOW) -> machine.Reading:
    return machine.Reading(value, at)


def good(**changes: object) -> machine.Readings:
    """Every probe answered and every link holds; ``changes`` swaps single readings."""
    base = machine.Readings(
        at=NOW,
        boot=reading(NOW - 3 * DAY),
        session=reading(machine.Session("signed-in", False)),
        updated_on=reading(date(2026, 11, 11)),
        tailscale=reading(machine.Tailscale("Running", True, NOW + 160 * DAY, False, ())),
        wsl_up_s=reading(5000.0),
        units=reading(machine.parse_units(units_text())),
        firstmate=reading(machine.Firstmate("alive", 300.0, "on", False)),
        claude_expiry=reading(NOW + 60 * DAY),
        net=reading(machine.Net(True, "api.example.test", 40)),
    )
    return replace(base, **{k: reading(v) for k, v in changes.items()})


UNKNOWN = machine.Unknown(machine.NOT_UNDERSTOOD)
GARBAGE = [
    "",
    "   \r\n",
    "garbage",
    "\x00\xff\x00",
    "{",
    "[]",
    "null",
    "<html>403</html>",
    "9" * 400,
]

# ---- parsers: sample output, garbage and empty ---------------------------------------------


def test_boot_time_is_read_from_powershells_round_trip_format() -> None:
    want = datetime(2026, 10, 21, 11, 7, 42, tzinfo=UTC).timestamp()
    assert machine.parse_boot(BOOT_OUT) == want
    assert machine.parse_boot("2026-10-21T11:07:42Z") == want


@pytest.mark.parametrize(
    "out", [*GARBAGE, "2026-13-45T99:99:99Z", "0001-01-01T00:00:00Z", "21/10/2026 11:07"]
)
def test_boot_time_garbage_is_unknown(out: str) -> None:
    assert machine.parse_boot(out) == UNKNOWN


@pytest.mark.parametrize(
    ("out", "want"),
    [
        (SESSION_OUT, machine.Session("locked", False)),
        ('{"logonui":[],"shell":[1],"cbs":false,"wu":false}', machine.Session("signed-in", False)),
        # PowerShell prints a single id as a bare number
        ('{"logonui":1,"shell":1,"cbs":false,"wu":true}', machine.Session("locked", True)),
        # one of two signed-in sessions is unlocked
        ('{"logonui":[1],"shell":[1,2],"cbs":true,"wu":false}', machine.Session("signed-in", True)),
        # only the sign-in screen is up
        ('{"logonui":[1],"shell":[],"cbs":false,"wu":false}', machine.Session("nobody", False)),
        ('{"logonui":null,"shell":null}', machine.Session("nobody", None)),
        ('{"logonui":[],"shell":[1],"cbs":"?","wu":true}', machine.Session("signed-in", True)),
        ('{"logonui":[],"shell":[1],"cbs":false}', machine.Session("signed-in", None)),
    ],
)
def test_session_and_restart_flags(out: str, want: machine.Session) -> None:
    assert machine.parse_session(out) == want


@pytest.mark.parametrize(
    "out",
    [*GARBAGE, '{"shell":[1]}', '{"shell":"x","logonui":[]}', '{"shell":[true],"logonui":[]}'],
)
def test_session_garbage_is_unknown(out: str) -> None:
    assert machine.parse_session(out) == UNKNOWN


def test_newest_update_date() -> None:
    assert machine.parse_updates(UPDATES_OUT) == date(2026, 10, 13)
    several = '[{"on":"2026-09-09"},{"on":"2026-10-13"},{"on":null},{"on":"bad"}]'
    assert machine.parse_updates(several) == date(2026, 10, 13)


@pytest.mark.parametrize("out", [*GARBAGE, '{"on":"2026-13-45"}', '{"on":20261013}', '{"x":1}'])
def test_update_date_garbage_is_unknown(out: str) -> None:
    assert machine.parse_updates(out) == UNKNOWN


def test_tailscale_state_key_dates_and_own_devices_only() -> None:
    ts = machine.parse_tailscale(tailscale_json())
    assert isinstance(ts, machine.Tailscale)
    assert (ts.backend, ts.online, ts.key_expired) == ("Running", True, False)
    assert ts.key_expiry == datetime(2027, 4, 20, 11, 58, 33, tzinfo=UTC).timestamp()
    assert [d.name for d in ts.others] == ["iphone-13", "ipad171"]  # by expiry; MagicDNS names
    # nothing identifying is carried: no login, no sign-in link, no other account's device
    assert not any(x in repr(ts) for x in (LOGIN, "PLANTED", "Some One", "someone-elses"))


def test_tailscale_logged_out_and_long_names() -> None:
    out = tailscale_json(BackendState="NeedsLogin", Self=None, Peer=None)
    assert machine.parse_tailscale(out) == machine.Tailscale("NeedsLogin", False, None, False, ())
    peer = {"HostName": "h" * 60, "DNSName": "", "UserID": 7, "KeyExpiry": "2026-12-01T00:00:00Z"}
    ts = machine.parse_tailscale(tailscale_json(Peer={"k": peer}))
    assert isinstance(ts, machine.Tailscale)
    assert len(ts.others[0].name) == machine.NAME_CHARS


@pytest.mark.parametrize(
    "out", [*GARBAGE, '{"BackendState":7}', '{"BackendState":"Run ning"}', '{"Self":{}}']
)
def test_tailscale_garbage_is_unknown(out: str) -> None:
    assert machine.parse_tailscale(out) == UNKNOWN


def test_uptime() -> None:
    assert machine.parse_uptime("1789007.56 17127065.80\n") == 1789007.56


@pytest.mark.parametrize("out", ["", "  ", "abc 1", "-5 1", "nan", "1e99"])
def test_uptime_garbage_is_unknown(out: str) -> None:
    assert machine.parse_uptime(out) == UNKNOWN


def test_systemd_units() -> None:
    units = machine.parse_units(units_text())
    assert isinstance(units, machine.Units)
    assert units.desk == machine.Unit(
        "loaded", "active", "success", 4242, NOW - 7200, None, 0, None
    )
    assert units.timer is not None and units.timer.last_trigger == NOW - 120
    assert units.service is not None and units.service.exited_at == NOW - 120
    missing = machine.parse_units(
        "Id=hive-fleet-up.timer\nLoadState=not-found\nActiveState=inactive\n"
    )
    assert isinstance(missing, machine.Units)
    assert missing.desk is None and missing.timer is not None
    assert missing.timer.load == "not-found"


@pytest.mark.parametrize("out", [*GARBAGE, "Id=\nLoadState=loaded\n", "LoadState=loaded\n"])
def test_systemd_garbage_is_unknown(out: str) -> None:
    assert machine.parse_units(out) == UNKNOWN


def test_claude_expiry_is_the_only_thing_taken_from_the_credentials() -> None:
    assert machine.parse_claude_expiry(credentials()) == CLAUDE_EXPIRY_MS / 1000
    assert machine.parse_claude_expiry(credentials(1795240278)) == 1795240278.0  # seconds


@pytest.mark.parametrize(
    "text",
    [*GARBAGE, credentials(None), credentials(True), credentials("soon"), credentials(-1), "{}"],
)
def test_claude_expiry_garbage_is_unknown_and_quotes_nothing(text: str) -> None:
    got = machine.parse_claude_expiry(text)
    assert isinstance(got, machine.Unknown)
    assert "PLANTED" not in got.why


def test_credentials_file_missing_unreadable_or_switched_off(tmp_path: Path) -> None:
    assert isinstance(machine.read_claude_expiry(tmp_path / "none.json"), machine.Unknown)
    assert machine.read_claude_expiry(None) == machine.Unknown(machine.SWITCHED_OFF)
    (tmp_path / "dir.json").mkdir()
    assert isinstance(machine.read_claude_expiry(tmp_path / "dir.json"), machine.Unknown)
    huge = tmp_path / "huge.json"
    huge.write_text(" " * (machine.MAX_CREDENTIALS_BYTES + 1))
    assert isinstance(machine.read_claude_expiry(huge), machine.Unknown)


# ---- probes: time-outs and failures -------------------------------------------------------


def test_every_probe_time_out_is_under_three_seconds() -> None:
    assert machine.PROBE_TIMEOUT_S < 3 and machine.NET_TIMEOUT_S < 3


async def test_a_command_that_hangs_is_killed_and_reads_unknown(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    hang = _exe(tmp_path / "hang", f"echo $$ > '{pid_file}'; exec sleep 30")
    start = time.monotonic()
    assert await machine.run([str(hang)], timeout_s=0.5) == machine.Unknown(machine.TIMED_OUT)
    assert time.monotonic() - start < 2.5
    with pytest.raises(ProcessLookupError):  # killed and reaped: nothing is left running
        os.kill(int(pid_file.read_text()), 0)


async def test_a_hung_commands_own_children_are_killed_with_it(tmp_path: Path) -> None:
    pid_file = tmp_path / "child"
    # the child inherits the output pipe and would hold it open long after its parent is gone
    script = _exe(tmp_path / "spawns", f"sleep 30 &\necho $! > '{pid_file}'\nwait")
    start = time.monotonic()
    assert await machine.run([str(script)], timeout_s=0.5) == machine.Unknown(machine.TIMED_OUT)
    assert time.monotonic() - start < 2.5
    child = int(pid_file.read_text())
    for _ in range(50):  # the kernel hands a killed orphan to init, which reaps it at once
        if not Path(f"/proc/{child}").exists():
            break
        await asyncio.sleep(0.02)
    assert not Path(f"/proc/{child}").exists()


async def test_a_command_that_fails_or_is_missing_reads_unknown(tmp_path: Path) -> None:
    noisy = _exe(tmp_path / "noisy", f"echo {SECRET}; echo {SECRET} >&2; exit 3")
    assert await machine.run([str(noisy)]) == machine.Unknown(machine.FAILED)  # output dropped
    assert await machine.run([str(tmp_path / "nope")]) == machine.Unknown(machine.NOT_STARTED)
    assert await machine.run([str(_exe(tmp_path / "ok", "echo fine"))]) == "fine\n"


async def test_reachability_is_one_connect_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with listening() as port:
        net = await machine.probe_net(("127.0.0.1", port))
    assert isinstance(net, machine.Net) and net.ok and net.ms is not None
    refused = await machine.probe_net(("127.0.0.1", closed_port()))
    assert refused == machine.Net(False, "127.0.0.1", why="No connection")
    assert await machine.probe_net(None) == machine.Unknown(machine.SWITCHED_OFF)

    async def hang(*_a: object, **_k: object) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(machine.asyncio, "open_connection", hang)
    start = time.monotonic()
    slow = await machine.probe_net(("192.0.2.1", 443), timeout_s=0.2)
    assert slow == machine.Net(False, "192.0.2.1", why="No answer in time")
    assert time.monotonic() - start < 1.5


async def test_a_round_reads_every_probe(tmp_path: Path) -> None:
    with listening() as port:
        r = rig(tmp_path, net=("127.0.0.1", port))
        readings = await monitor_for(r).fresh()
    by = rows(view_of(readings))
    assert {k: v.state for k, v in by.items()} == {
        "power": "ok",
        "session": "ok",
        "tailscale": "ok",
        "wsl": "ok",
        "desk": "ok",
        "fleet": "ok",
        "firstmate": "ok",
        "claude": "warn",  # 10 days left: inside the 14-day band
        "internet": "ok",
    }
    assert by["power"].headline == "On for 20 d 17 h"
    assert by["session"].headline == "Locked"
    assert (by["tailscale"].headline, by["tailscale"].detail) == (
        "Connected",
        "Key good for 160 days",
    )
    assert by["wsl"].headline == "Up 1 h 23 min"
    assert by["desk"].detail == "Running as its service"
    assert (by["fleet"].headline, by["fleet"].detail) == ("On", "Last run succeeded")
    assert (by["firstmate"].headline, by["firstmate"].detail) == ("Alive", "Watchdog off")
    assert by["firstmate"].moment == machine.Moment("last activity", NOW - 300)
    assert by["claude"].headline == "Valid, 10 days left" and not by["claude"].urgent
    assert by["claude"].moment == machine.Moment("expires", CLAUDE_EXPIRY_MS / 1000, exact=True)
    assert all(row.checked_at == NOW for row in by.values())
    # nothing from the credentials file or the Tailscale account travels with the readings
    assert not any(x in repr(readings) for x in ("PLANTED", LOGIN))


async def test_hung_probes_time_out_to_unknown_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(machine, "PROBE_TIMEOUT_S", 0.4)
    r = rig(tmp_path)
    r.slow.touch()
    start = time.monotonic()
    readings = await monitor_for(r).fresh()
    assert time.monotonic() - start < 3
    view = view_of(readings)
    by = rows(view)
    for key in ("power", "session", "tailscale", "fleet"):
        assert (by[key].state, by[key].headline) == ("unknown", "Unknown"), key
        assert by[key].detail == "The check did not answer in time"
    assert by["desk"].detail == "Service state unknown: the check did not answer in time"
    assert by["internet"].detail == "This check is switched off"
    assert {k.label: k.state for k in view.risks}["Windows restart"] == "unknown"


async def test_peek_never_waits_and_slow_facts_are_reused(tmp_path: Path) -> None:
    r, clock, changes = rig(tmp_path), Clock(), []
    mon = monitor_for(r, clock=clock, on_change=lambda: changes.append(1))
    r.slow.touch()
    start = time.monotonic()
    assert mon.peek() is None  # nothing read yet, and the hung round is not waited for
    assert time.monotonic() - start < 0.2
    await mon.stop()
    r.slow.unlink()

    first = await mon.fresh()
    assert sorted(r.launched()) == ["boot", "session", "updates"]
    assert mon.peek() is first and sorted(r.launched()) == ["boot", "session", "updates"]

    clock.at += r.settings.machine_ttl_s + 1  # stale: the old readings answer, a round starts
    assert mon.peek() is first
    second = await mon.fresh()
    assert second.at == clock.at and second.session.at == clock.at
    assert second.boot is first.boot and second.updated_on is first.updated_on
    assert sorted(r.launched()) == ["boot", "session", "session", "updates"]

    clock.at += machine.SLOW_TTL_S + 1  # now the slow facts are read again too
    third = await mon.fresh()
    assert third.boot.at == clock.at
    assert r.launched().count("boot") == 2 and r.launched().count("updates") == 2
    assert len(changes) == 3  # each finished round is announced to open pages


async def test_a_failed_slow_fact_is_retried_next_round(tmp_path: Path) -> None:
    r, clock = rig(tmp_path), Clock()
    r.settings.powershell.write_text("#!/usr/bin/env bash\nexit 1\n")  # type: ignore[union-attr]
    mon = monitor_for(r, clock=clock)
    assert (await mon.fresh()).boot.value == machine.Unknown(machine.FAILED)
    r.settings.powershell.write_text(f"#!/usr/bin/env bash\nprintf '%s' '{BOOT_OUT}'\n")  # type: ignore[union-attr]
    clock.at += 30
    assert (await mon.fresh()).boot.value == machine.parse_boot(BOOT_OUT)


async def test_firstmate_that_cannot_be_read_is_unknown(tmp_path: Path) -> None:
    r = rig(tmp_path)

    def broken(_home: Path) -> FirstmateStatus:
        raise OSError("no /proc")

    mon = machine.MachineMonitor(
        r.settings, StatusCache(r.settings, reader=broken), lambda: WATCHDOG_OFF, None, Clock()
    )
    readings = await mon.fresh()
    assert isinstance(readings.firstmate.value, machine.Unknown)
    assert rows(view_of(readings))["firstmate"].state == "unknown"


def test_the_bot_and_the_desk_find_tailscale_the_same_way(tmp_path: Path) -> None:
    assert tailscale_binary({"HIVE_TAILSCALE_BIN": str(tmp_path / "ts")}) == tmp_path / "ts"
    assert tailscale_binary({"HIVE_TAILSCALE_BIN": "off"}) is None


def test_settings_read_the_probe_sources_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIVE_GATEWAY_POWERSHELL", "off")
    monkeypatch.setenv("HIVE_TAILSCALE_BIN", "off")
    monkeypatch.setenv("HIVE_GATEWAY_SYSTEMCTL", str(tmp_path / "systemctl"))
    monkeypatch.setenv("HIVE_GATEWAY_CLAUDE_CREDENTIALS", str(tmp_path / "c.json"))
    monkeypatch.setenv("HIVE_GATEWAY_NET_CHECK", "Example.TEST:8443")
    s = GatewaySettings.from_env()
    assert (s.powershell, s.tailscale, s.systemctl) == (None, None, tmp_path / "systemctl")
    assert s.claude_credentials == tmp_path / "c.json" and s.net_check == ("example.test", 8443)
    for bad in ("off", "no-port", "host:0", "host:99999", "http://host:443", "a b:443"):
        monkeypatch.setenv("HIVE_GATEWAY_NET_CHECK", bad)
        assert GatewaySettings.from_env().net_check is None, bad
    monkeypatch.setenv("HIVE_GATEWAY_CLAUDE_CREDENTIALS", "off")
    assert GatewaySettings.from_env().claude_credentials is None
    # a settings object built by hand (every test) probes nothing real
    bare = GatewaySettings()
    assert (bare.powershell, bare.tailscale, bare.systemctl) == (None, None, None)
    assert bare.claude_credentials is None and bare.net_check is None


# ---- redaction ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        SECRET,
        "tskey-auth-kPLANTED1CNTRL-abcdefghijklmnop",
        "ghp_" + "a" * 36,
        "github_pat_11ABCDEFG0abcdefghij",
        "xoxb-1234-abcd",
        "Bearer abc.def.ghi",
        LOGIN,
        AUTH_URL,
        "123e4567-e89b-12d3-a456-426614174000",
        "a3f9" * 10,
        "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=",
    ],
)
def test_redact_removes_anything_token_like(secret: str) -> None:
    out = machine.redact(f"before {secret} after")
    assert secret not in out and out == "before [redacted] after"


def test_redact_keeps_plain_wording_clips_and_drops_control_characters() -> None:
    plain = [
        "Valid, 10 days left",
        "Key good for 160 days",
        "Reached api.anthropic.com in 39 ms",
        "Tailscale key, ipad171",
        "Service state unknown: the systemctl command was not found",
        "November updates are out but not installed: expect a restart within days",
    ]
    assert [machine.redact(t) for t in plain] == plain
    assert len(machine.redact("word " * 100)) <= 160
    assert machine.redact("a\x00b\nc\x1b[31m") == "a b c [31m"


def _ctx() -> pages.Ctx:
    return pages.Ctx(CSRF.csrf(), nxt="/machine")


def test_a_token_planted_in_any_field_never_reaches_the_page() -> None:
    moment = machine.Moment(SECRET, NOW)
    row = machine.Row(SECRET, SECRET, SECRET, SECRET, SECRET, NOW, moment, True)
    risk = machine.Risk(SECRET, SECRET, SECRET, SECRET, moment)
    for obj in (row, risk, moment):
        assert "PLANTED" not in repr(obj)
    html = pages.render_machine(machine.MachineView((row,), (risk,), NOW, SECRET), _ctx())
    assert "PLANTED" not in html and html.count("[redacted]") >= 8


async def test_secrets_in_probe_output_never_reach_the_page(tmp_path: Path) -> None:
    peers = {
        "k1": {  # a device whose name is itself token-like
            "HostName": "localhost",
            "DNSName": "sk-ant-PLANTED.tail0000.ts.net.",
            "UserID": 7,
            "KeyExpiry": "2026-12-16T13:49:26Z",
        },
        "k2": {
            "HostName": LOGIN,
            "DNSName": "",
            "UserID": 7,
            "KeyExpiry": "2026-12-20T13:49:26Z",
        },
    }
    r = rig(tmp_path, tailscale=tailscale_json(Peer=peers))
    view = view_of(await monitor_for(r).fresh())
    html = pages.render_machine(view, _ctx())
    assert not any(x in html for x in ("PLANTED", LOGIN, "Some One", "tail0000"))
    assert html.count("Tailscale key, [redacted]") == 2


def test_the_pages_own_wording_is_never_redacted() -> None:
    bad = good(
        session=machine.Session("nobody", True),
        tailscale=machine.Tailscale(
            "Running", False, NOW + 9 * DAY, False, (machine.Device("ipad171", NOW + 2 * DAY),)
        ),
        units=machine.parse_units(units_text(result="exit-code", status=1)),
        firstmate=machine.Firstmate("down", 7200.0, "off (config unusable)", True),
        claude_expiry=NOW + 2 * DAY,
        net=machine.Net(False, "api.anthropic.com", why="No answer in time"),
    )
    lost = good(**{f: UNKNOWN for f in ("boot", "session", "tailscale", "units", "claude_expiry")})
    for readings in (good(), bad, lost, None):
        view = machine.build_view(readings, NOW, NOW - 4000, 4242)
        assert "[redacted]" not in pages.render_machine(view, _ctx())
    for backend in ("NeedsLogin", "Stopped", "NeedsMachineAuth", "Starting", "Odd"):
        gone = good(tailscale=machine.Tailscale(backend, False, None, False, ()))
        assert "[redacted]" not in pages.render_machine(view_of(gone), _ctx()), backend


# ---- the rules behind each row ------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "state", "urgent", "headline"),
    [
        (60, "ok", False, "Valid, 60 days left"),
        (14.5, "ok", False, "Valid, 14 days left"),
        (14, "warn", False, "Valid, 14 days left"),  # the warning band starts at 14 days
        (3.5, "warn", False, "Valid, 3 days left"),
        (3, "warn", True, "Valid, 3 days left"),  # the red band starts at 3 days
        (0.5, "warn", True, "Valid, 12 h left"),
        (0.01, "warn", True, "Valid, under an hour left"),
        (0, "down", True, "Expired"),
        (-5, "down", True, "Expired"),
    ],
)
def test_claude_sign_in_bands(days: float, state: str, urgent: bool, headline: str) -> None:
    row = rows(view_of(good(claude_expiry=NOW + days * DAY)))["claude"]
    assert (row.state, row.urgent, row.headline) == (state, urgent, headline)


@pytest.mark.parametrize(
    ("ts", "state", "headline"),
    [
        (machine.Tailscale("Running", True, NOW + 160 * DAY, False, ()), "ok", "Connected"),
        (machine.Tailscale("Running", True, None, False, ()), "ok", "Connected"),
        (machine.Tailscale("Running", True, NOW + 9 * DAY, False, ()), "warn", "Connected"),
        (
            machine.Tailscale("Running", False, NOW + 160 * DAY, False, ()),
            "warn",
            "Running, offline",
        ),
        (machine.Tailscale("Running", True, NOW - DAY, False, ()), "down", "Key expired"),
        (machine.Tailscale("NeedsLogin", False, NOW - DAY, True, ()), "down", "Key expired"),
        (machine.Tailscale("NeedsLogin", False, None, False, ()), "down", "Logged out"),
        (machine.Tailscale("Stopped", False, None, False, ()), "down", "Turned off"),
        (machine.Tailscale("Starting", False, None, False, ()), "warn", "Starting"),
        (machine.Tailscale("Odd", True, None, False, ()), "unknown", "Unknown"),
    ],
)
def test_tailscale_row(ts: machine.Tailscale, state: str, headline: str) -> None:
    row = rows(view_of(good(tailscale=ts)))["tailscale"]
    assert (row.state, row.headline) == (state, headline)


def test_session_row() -> None:
    by = rows(view_of(good(session=machine.Session("nobody", False))))
    assert (by["session"].state, by["session"].headline) == ("warn", "Nobody signed in")
    by = rows(view_of(good(session=machine.Session("locked", False))))
    assert (by["session"].state, by["session"].headline) == ("ok", "Locked")
    assert rows(view_of(good(session=machine.Session("odd", False))))["session"].state == "unknown"


@pytest.mark.parametrize(
    ("text", "pid", "state", "detail"),
    [
        (units_text(), 4242, "ok", "Running as its service"),
        (units_text(), 999, "ok", "Started by hand; its service runs separately"),
        (
            units_text().replace(
                "ActiveState=active\nResult=success\nMainPID",
                "ActiveState=failed\nResult=x\nMainPID",
                1,
            ),
            4242,
            "warn",
            "Started by hand; its service is failed",
        ),
        (
            "Id=hive-fleet-up.timer\nLoadState=loaded\nActiveState=active\n",
            4242,
            "warn",
            "Not installed as a service: it will not start by itself",
        ),
    ],
)
def test_desk_row(text: str, pid: int, state: str, detail: str) -> None:
    row = rows(view_of(good(units=machine.parse_units(text)), pid=pid))["desk"]
    assert (row.state, row.headline, row.detail) == (state, "Up 1 h 6 min", detail)


@pytest.mark.parametrize(
    ("text", "state", "headline"),
    [
        (units_text(), "ok", "On"),
        (units_text(timer_load="not-found", timer="inactive"), "down", "Not installed"),
        (units_text(timer="inactive"), "down", "Off"),
        (units_text(result="exit-code", status=1), "warn", "Last run failed"),
        (units_text(last=NOW - 1200), "warn", "Overdue"),
        (units_text(last=None), "warn", "No run yet"),
        (
            "Id=hive-gateway.service\nLoadState=loaded\nActiveState=active\n",
            "down",
            "Not installed",
        ),
    ],
)
def test_fleet_up_timer_row(text: str, state: str, headline: str) -> None:
    row = rows(view_of(good(units=machine.parse_units(text))))["fleet"]
    assert (row.state, row.headline) == (state, headline)


@pytest.mark.parametrize(
    ("fm", "state", "headline", "detail"),
    [
        (machine.Firstmate("alive", 60.0, "on", False), "ok", "Alive", "Watchdog on"),
        (machine.Firstmate("alive", 60.0, "off", False), "ok", "Alive", "Watchdog off"),
        (machine.Firstmate("alive", 60.0, "halted", True), "warn", "Alive", "Watchdog halted"),
        (machine.Firstmate("no-beat", 60.0, "on", False), "warn", "Watcher silent", "Watchdog on"),
        (machine.Firstmate("down", None, "off", False), "down", "Down", "Watchdog off"),
        (
            machine.Firstmate("odd", None, "off", False),
            "unknown",
            "Unknown",
            "The answer was not understood",
        ),
    ],
)
def test_firstmate_row(fm: machine.Firstmate, state: str, headline: str, detail: str) -> None:
    row = rows(view_of(good(firstmate=fm)))["firstmate"]
    assert (row.state, row.headline, row.detail) == (state, headline, detail)
    assert (row.moment is None) == (fm.idle_s is None)


def test_internet_row() -> None:
    by = rows(view_of(good()))
    assert (by["internet"].state, by["internet"].detail) == (
        "ok",
        "Reached api.example.test in 40 ms",
    )
    down = good(net=machine.Net(False, "api.example.test", why="No connection"))
    row = rows(view_of(down))["internet"]
    assert (row.state, row.headline) == ("down", "Not reachable")
    assert row.detail == "No connection from api.example.test"


def test_missing_and_old_readings_show_unknown_never_ok() -> None:
    never = machine.build_view(None, NOW, NOW - 10)
    assert never.checked_at is None and never.risks == () and never.note == "Not checked yet"
    assert [(r.state, r.detail) for r in never.rows] == [("unknown", "Not checked yet")] * 9
    assert [r.label for r in never.rows] == [label for _key, label in machine.CHAIN]

    fresh = machine.build_view(good(), NOW + machine.STALE_S, NOW - 10, 4242)
    assert all(r.state == "ok" for r in fresh.rows)
    old = machine.build_view(good(), NOW + machine.STALE_S + 1, NOW - 10, 4242)
    assert [(r.state, r.headline) for r in old.rows] == [("unknown", "Checking")] * 9
    assert old.checked_at == NOW and old.risks == ()


def test_a_single_unknown_probe_leaves_the_other_rows_alone() -> None:
    by = rows(view_of(good(boot=machine.Unknown(machine.TIMED_OUT))))
    assert (by["power"].state, by["power"].detail) == (
        "unknown",
        "The check did not answer in time",
    )
    assert all(row.state == "ok" for key, row in by.items() if key != "power")


def test_patch_tuesday_is_the_second_tuesday() -> None:
    assert machine.patch_tuesday(2026, 10).date() == date(2026, 10, 13)
    assert machine.patch_tuesday(2026, 11).date() == date(2026, 11, 10)
    assert machine.patch_tuesday(2026, 12).date() == date(2026, 12, 8)
    assert machine.patch_tuesday(2027, 6).date() == date(2027, 6, 8)  # the 1st is a Tuesday
    released, upcoming = machine.patch_tuesdays(NOW)
    assert (released.date(), upcoming.date()) == (date(2026, 11, 10), date(2026, 12, 8))
    early = datetime(2026, 11, 3, tzinfo=UTC).timestamp()
    assert [d.date() for d in machine.patch_tuesdays(early)] == [
        date(2026, 10, 13),
        date(2026, 11, 10),
    ]
    january = datetime(2027, 1, 2, tzinfo=UTC).timestamp()
    assert [d.date() for d in machine.patch_tuesdays(january)] == [
        date(2026, 12, 8),
        date(2027, 1, 12),
    ]


def _restart(**changes: object) -> machine.Risk:
    return next(k for k in view_of(good(**changes)).risks if k.label == "Windows restart")


def test_windows_restart_risk() -> None:
    calm = _restart()
    assert (calm.state, calm.due, calm.detail) == ("ok", "in 27 days", "None pending")
    assert calm.moment is not None and calm.moment.at == machine.patch_tuesday(2026, 12).timestamp()
    pending = _restart(session=machine.Session("locked", True))
    assert (pending.state, pending.due, pending.order) == ("warn", "now", NOW)
    behind = _restart(updated_on=date(2026, 10, 13))
    assert (behind.state, behind.due) == ("warn", "within days")
    assert behind.detail.startswith("November updates are out but not installed")
    unreadable = _restart(session=machine.Unknown(machine.TIMED_OUT))
    assert (unreadable.state, unreadable.due) == ("unknown", "in 27 days")
    no_dates = _restart(updated_on=machine.Unknown(machine.TIMED_OUT))
    assert no_dates.state == "ok"  # the restart flags were read; only the update date was not


def test_upcoming_risks_are_sorted_nearest_first_with_undated_last() -> None:
    ts = machine.Tailscale(
        "Running",
        True,
        None,  # this PC's key does not expire: undated
        False,
        (
            machine.Device("old-phone", NOW - 40 * DAY),  # already expired: not upcoming
            machine.Device("ipad171", NOW + 35 * DAY),
            machine.Device("laptop", NOW + 2 * DAY),
        ),
    )
    view = view_of(good(tailscale=ts, claude_expiry=NOW + 10 * DAY))
    assert [(k.label, k.state, k.due) for k in view.risks] == [
        ("Tailscale key, laptop", "warn", "in 2 days"),
        ("Claude sign-in", "warn", "in 10 days"),
        ("Windows restart", "ok", "in 27 days"),
        ("Tailscale key, ipad171", "ok", "in 35 days"),
        ("Tailscale key, this PC", "ok", "no date"),
    ]
    assert view.risks[0].urgent and not view.risks[1].urgent
    lost = view_of(good(tailscale=UNKNOWN, claude_expiry=UNKNOWN))
    assert [(k.label, k.state) for k in lost.risks][1:] == [
        ("Tailscale key, this PC", "unknown"),
        ("Claude sign-in", "unknown"),
    ]
    gone = view_of(good(claude_expiry=NOW - DAY))
    assert [(k.label, k.state, k.due) for k in gone.risks][0] == (
        "Claude sign-in",
        "down",
        "expired",
    )


def test_spans_and_time_left() -> None:
    assert [machine.span(s) for s in (5, 60, 3725, 90000, 20 * DAY + 17 * 3600 + 59)] == [
        "under a minute",
        "1 min",
        "1 h 2 min",
        "1 d 1 h",
        "20 d 17 h",
    ]
    assert [machine.left(s) for s in (10, 3600, DAY, 2 * DAY - 1, 2 * DAY, 10.9 * DAY)] == [
        "under an hour",
        "1 h",
        "1 day",
        "1 day",
        "2 days",
        "10 days",
    ]


# ---- the page -----------------------------------------------------------------------------


def _main(html: str) -> str:
    m = re.search(r"<main>(.*)</main>", html, re.S)
    assert m
    return m.group(1)


def _settings(home: Path, tmp_path: Path) -> GatewaySettings:
    return GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=home,
        snapshot_ttl_s=60,
        data_dir=tmp_path / "data",
        live=False,
    )


def _client(app) -> TestClient:
    return TestClient(app, client=("127.0.0.1", 5000), follow_redirects=False)


def _get_until(c: TestClient, gone: str, tries: int = 100) -> str:
    """The Machine page once ``gone`` has left it (a probe round landed)."""
    for _ in range(tries):
        html = c.get("/machine", headers=GOOD).text
        if gone not in html:
            return html
        time.sleep(0.05)
    raise AssertionError(f"{gone!r} never left the page")


def test_the_page_renders_with_every_probe_unknown(home: Path, tmp_path: Path) -> None:
    settings = _settings(home, tmp_path)  # no PowerShell, Tailscale, systemctl, sign-in or target

    def broken(_home: Path) -> FirstmateStatus:
        raise OSError("no /proc")

    monitor = machine.MachineMonitor(
        settings,
        StatusCache(settings, reader=broken),
        lambda: WATCHDOG_OFF,
        proc_uptime=tmp_path / "no-uptime",
    )
    with _client(create_app(settings, tokens=CSRF, monitor=monitor)) as c:
        first = c.get("/machine", headers=GOOD)
        assert first.status_code == 200
        main = _main(first.text)
        assert main.count("mrow--unknown") == 9 and main.count("Not checked yet") == 10
        assert (
            "mrow--ok" not in main and "<li class='mstate mstate--unknown'>9 Unknown</li>" in main
        )

        main = _main(_get_until(c, "Not checked yet"))
    assert main.count("mrow--unknown") == 8 + 3  # all but the desk itself, plus three risks
    assert main.count("mrow--ok") == 1 and "Service state unknown" in main
    for why in (
        "PowerShell was not found",
        "Tailscale&#x27;s command was not found",
        "Its record was not found",
        "Its records could not be read",
        "This check is switched off",
        "Could not read whether a restart is pending",
    ):
        assert why in main, why
    assert "error" not in main.lower() and "Traceback" not in main


def test_the_page_is_read_only_owner_only_and_live(home: Path, tmp_path: Path) -> None:
    app = create_app(_settings(home, tmp_path), tokens=CSRF)
    routes = [r for r in app.routes if "machine" in getattr(r, "path", "")]
    assert [(r.path, r.methods) for r in routes] == [("/machine", {"GET"})]  # nothing writes
    with _client(app) as c:
        assert c.get("/machine", headers={"host": HOST}).status_code == 403
        assert c.get("/machine", headers={**GOOD, "host": "evil.example"}).status_code == 403
        assert c.post("/machine", headers=GOOD).status_code == 405
        assert c.put("/machine", headers=GOOD).status_code == 405
        res = c.get("/machine", headers=GOOD)
    assert res.status_code == 200
    main = _main(res.text)
    assert not re.search(r"<(form|button|input|select|textarea)\b", main)  # nothing to press
    assert "<a href='/machine' aria-current=page>Machine</a>" in res.text
    assert "data-refresh=30000 data-machine" in res.text
    assert pages.SCRIPT_CSP_HASH in res.headers["content-security-policy"]
    assert _safe_next("/machine") == "/machine"  # the header's forms return here


def test_a_finished_round_is_announced_on_the_live_stream(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(LiveHub, "publish", lambda _self, scope: seen.append(scope))
    with _client(create_app(_settings(home, tmp_path), tokens=CSRF)) as c:
        _get_until(c, "Not checked yet")
    assert "machine" in seen


async def test_the_page_shows_states_as_words_and_dates_in_full(tmp_path: Path) -> None:
    with listening() as port:
        view = view_of(await monitor_for(rig(tmp_path, net=("127.0.0.1", port))).fresh())
    main = _main(pages.render_machine(view, _ctx()))
    assert main.count("<li class='mrow mrow--") == 9 + 4
    assert "<span class='mstate mstate--warn'>Warning</span>" in main  # a word, not colour alone
    assert "<li class='mstate mstate--warn'>1 Warning</li>" in main
    assert "<li class='mstate mstate--ok'>8 OK</li>" in main
    assert ">21 Nov 2026, 4:51 PM</time>" in main and "class=date" in main  # AEDT, with its year
    assert "<span class=mrow__side>checked <time datetime=" in main
    assert "<span class='mrow__side mrow__due'>in 10 days</span>" in main
    urgent = pages.render_machine(view_of(good(claude_expiry=NOW + 2 * DAY)), _ctx())
    assert "<li class='mrow mrow--warn mrow--urgent'>" in urgent
    down = pages.render_machine(view_of(good(claude_expiry=NOW - DAY)), _ctx())
    assert "<span class='mstate mstate--down'>Down</span>" in down


# ---- the page in a real browser -----------------------------------------------------------

LAYOUT_DRIVER = """
var f=q('#f');
if(!(f.contentDocument&&f.contentDocument.querySelector('.mach')))
await new Promise(function(r){f.addEventListener('load',r);});
var w=f.contentWindow,d=w.document,e=d.documentElement,c=d.querySelectorAll('.mach__col');
function all(s){return [].slice.call(d.querySelectorAll(s));}
var a=d.getElementById('alerts');a.hidden=false;a.textContent='Alerts off';
R.iw=w.innerWidth;R.ih=w.innerHeight;R.sw=e.scrollWidth;R.sh=e.scrollHeight;
R.rows=all('.mrow').length;
R.sideBySide=c[1].getBoundingClientRect().left>c[0].getBoundingClientRect().right-1;
R.cut=all('.chrome *,.mach *').filter(function(x){
return x.offsetParent&&x.getBoundingClientRect().right>w.innerWidth+0.5;}).length;
R.navTops=all('.chrome__brand,.chrome nav a,#alerts').map(function(x){
return Math.round(x.getBoundingClientRect().top);});
R.chips=all('.mrow .mstate').map(function(x){return Math.round(x.getBoundingClientRect().width);});
R.end=Math.round(d.querySelector('.mach').getBoundingClientRect().bottom);
"""


# The page shows each time against the browser's clock ("just now", or a weekday and date once
# it is not today), so a layout measured against the real clock changes length day by day.
# The framed page's clock is held at the readings' time, 30 s after NOW.
FROZEN_CLOCK = (
    f"(function(){{var D=Date,T={int((NOW + 30) * 1000)};"
    "function F(){if(!arguments.length)return new D(T);"
    "return new (Function.prototype.bind.apply(D,[null].concat([].slice.call(arguments))))();}"
    "F.now=function(){return T;};F.parse=D.parse;F.UTC=D.UTC;F.prototype=D.prototype;"
    "window.Date=F;})();"
)


def _framed(tmp_path: Path, page: str, size: tuple[int, int]) -> dict:
    """Lay the page out in a viewport of exactly ``size`` and measure it.

    Headless Chrome will not open a window narrower than 500 px, so the page goes in a frame of
    the wanted size: its media queries and its scrolling answer to the frame, as on a device.
    Its clock is ``FROZEN_CLOCK``, so the times it shows are the same on any day.
    """
    import html as html_mod

    page = page.replace("<script>", f"<script>{FROZEN_CLOCK}</script><script>", 1)
    host = (
        "<!doctype html><html><body style='margin:0'>"
        f"<iframe id=f width={size[0]} height={size[1]} style='border:0;display:block' "
        f'srcdoc="{html_mod.escape(page, quote=True)}"></iframe></body></html>'
    )
    return _in_chrome(tmp_path, host, LAYOUT_DRIVER, (1400, 1400))


def _busy_view() -> machine.MachineView:
    """The worst day: long wording in most rows, two urgent bands, five risks."""
    ts = machine.Tailscale(
        "Running", True, NOW + 9 * DAY, False, (machine.Device("ipad171", NOW + 35 * DAY),)
    )
    readings = good(
        session=machine.Session("nobody", True),
        updated_on=date(2026, 10, 13),
        tailscale=ts,
        units=machine.parse_units(units_text(result="exit-code", status=1)),
        firstmate=machine.Firstmate("down", 7200.0, "off (config unusable)", True),
        claude_expiry=NOW + 2 * DAY,
        net=machine.Net(False, "api.anthropic.com", why="No answer in time"),
    )
    return view_of(readings)


SIZES = {
    "slide-over": (320, 640),
    "phone-360": (360, 740),
    "phone-390": (390, 844),
    "split-507": (507, 900),
    "split-694": (694, 900),
    "ipad-portrait": (820, 1180),
    "ipad-landscape": (1180, 820),
}


@needs_chrome
@pytest.mark.parametrize("name", SIZES)
@pytest.mark.parametrize("view", ["usual", "busy"])
def test_the_page_fits_phone_and_ipad_without_sideways_scroll(
    tmp_path: Path, name: str, view: str
) -> None:
    size = SIZES[name]
    shown = _busy_view() if view == "busy" else view_of(good(claude_expiry=NOW + 10 * DAY))
    r = _framed(tmp_path, pages.render_machine(shown, _ctx()), size)
    assert (r["iw"], r["ih"]) == size, r
    assert r["rows"] == 9 + len(shown.risks)
    assert r["sw"] <= r["iw"] and r["cut"] == 0, r  # no sideways scroll, nothing cut off
    # the header's links, with the alerts button at its widest, never break onto two rows;
    # they share the brand's row (asserted from 360 px: at 320 the fit is a few pixels and
    # this browser has only fallback fonts, which run wider than the desk's own)
    assert len(set(r["navTops"][1:])) == 1, r
    if size[0] >= 360:
        assert len(set(r["navTops"])) == 1, r
    assert len(set(r["chips"])) == 1, r  # equal chips, so the labels line up down the page
    assert r["sideBySide"] is (name == "ipad-landscape")
    if name.startswith("ipad") and view == "usual":
        assert r["sh"] <= r["ih"], r  # the whole chain and its risks in one screen


LIVE_DRIVER = """
var n=0;window.fetch=function(){n++;return Promise.resolve({ok:true,
text:function(){return Promise.resolve(window.__next);}});};
window.__next=document.documentElement.outerHTML.replace('Nobody signed in','Signed in');
window.__es.onopen();
R.before=q('main').textContent.indexOf('Nobody signed in')>=0;
window.__es.l.machine();
await new Promise(function(r){setTimeout(r,150);});
R.swapped=q('main').textContent.indexOf('Nobody signed in')<0;
R.stamped=q('.mrow__side time').textContent;
R.date=q('time.date').textContent;
var was=n;await new Promise(function(r){setTimeout(r,700);});
R.polls=n-was;
"""


@needs_chrome
def test_an_open_page_refreshes_on_the_machine_event_and_keeps_polling(tmp_path: Path) -> None:
    now = time.time()
    view = machine.build_view(
        good(session=machine.Session("nobody", False), claude_expiry=now + 10 * DAY),
        NOW,
        NOW - 60,
        4242,
    )
    page = pages.render_machine(view, _ctx()).replace("data-refresh=30000", "data-refresh=200")
    r = _in_chrome(tmp_path, page, LIVE_DRIVER, (820, 1180), prelude=FAKE_LIVE)
    assert r["before"] and r["swapped"]  # the machine event re-rendered the rows in place
    assert re.fullmatch(r"\d{1,2} \w{3} \d{4}, \d{1,2}:\d\d [ap]m", r["date"]), r["date"]
    assert r["stamped"]
    # while the stream is live the page still polls: each poll is what starts a probe round
    assert r["polls"] >= 2, r


@needs_chrome
def test_other_pages_ignore_the_machine_event(tmp_path: Path) -> None:
    driver = (
        "var n=0;window.fetch=function(){n++;return Promise.resolve({ok:false});};"
        "window.__es.onopen();window.__es.l.machine();"
        "await new Promise(function(r){setTimeout(r,200);});R.fetched=n;"
    )
    page = pages.render_machine(_busy_view(), _ctx()).replace(" data-machine", "")
    r = _in_chrome(tmp_path, page, driver, (820, 1180), prelude=FAKE_LIVE)
    assert r["fetched"] == 0


# ---- the gone-dark alarm line ---------------------------------------------------------------


def _alarm(on: bool, result: str | None = None, reason: str = "", age: float = 60.0):
    last = gonedark.Outcome(NOW - age, result, reason) if result else None
    return machine.Alarm(on, last, 300)


@pytest.mark.parametrize(
    ("alarm", "state", "headline"),
    [
        (_alarm(False), "warn", "Off"),
        (_alarm(True), "warn", "On, no ping yet"),
        (_alarm(True, "sent"), "ok", "On"),
        (_alarm(True, "skipped", "the desk is not answering"), "warn", "Ping skipped"),
        (_alarm(True, "failed", "the ping timed out"), "warn", "Last ping failed"),
        (_alarm(True, "sent", age=661.0), "warn", "On, not running"),
        (machine.Unknown(machine.FAILED), "unknown", "Unknown"),
    ],
)
def test_alarm_line_states(alarm: object, state: str, headline: str) -> None:
    row = view_of(good(gonedark=alarm)).alarm
    assert row is not None and (row.state, row.headline) == (state, headline)


def test_alarm_line_is_left_out_when_not_read() -> None:
    assert view_of(good()).alarm is None


def test_alarm_reads_only_whether_the_config_exists(tmp_path: Path) -> None:
    env, state = tmp_path / "gonedark.env", tmp_path / "state"
    assert machine.read_alarm(env, state) == machine.Alarm(False, None, 300)
    env.write_text("HIVE_GONEDARK_URL=https://hc-ping.example/PLANTED-SECRET\n")
    env.chmod(0)  # unreadable: the desk never needs to open it
    state.mkdir()
    (state / gonedark.STATE_FILE).write_text(
        json.dumps(
            {"at": NOW, "result": "skipped", "reason": gonedark.NO_FIRSTMATE, "interval_s": 120}
        )
    )
    got = machine.read_alarm(env, state)
    assert got == machine.Alarm(True, gonedark.Outcome(NOW, "skipped", gonedark.NO_FIRSTMATE), 120)


async def test_the_page_shows_the_alarm_line_and_never_the_address(tmp_path: Path) -> None:
    env, state = tmp_path / "gonedark.env", tmp_path / "gd-state"
    env.write_text("HIVE_GONEDARK_URL=https://hc-ping.example/PLANTED-SECRET\n")
    state.mkdir()
    (state / gonedark.STATE_FILE).write_text(
        json.dumps({"at": NOW - 120, "result": "sent", "reason": "", "interval_s": 300})
    )
    r = rig(tmp_path, gonedark_env=env, gonedark_state=state)
    view = view_of(await monitor_for(r).fresh())
    main = _main(pages.render_machine(view, _ctx()))
    assert "<h2 class=sec-label>Gone-dark alarm</h2>" in main
    assert "Gone-dark alarm</b><span class=mrow__head>On</span>" in main
    assert "Last ping sent" in main and "last ping" in main
    assert "PLANTED" not in main and "hc-ping" not in main
    # The alarm is not a link in the chain: the summary still counts the nine.
    summary = re.search(r"<ul class=mach__sum.*?</ul>", main, re.S)
    assert summary is not None
    assert sum(map(int, re.findall(r">(\d+) ", summary.group(0)))) == 9
