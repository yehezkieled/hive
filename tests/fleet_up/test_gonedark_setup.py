"""scripts/gonedark-setup.sh and the config checks in scripts/hive-gonedark.sh.

Everything runs against a temporary HOME; the restart and stop commands are stubs that
record they ran, so no service is touched and no ping is ever sent.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.fleet_up.test_fleet_up import ROOT

SETUP = ROOT / "scripts" / "gonedark-setup.sh"
WRAPPER = ROOT / "scripts" / "hive-gonedark.sh"
CANARY = "canary-5d1c0b7e9a2f4e83"
URL = f"https://hc-ping.example/{CANARY}"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path


def _env(home: Path, **extra: str) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("HIVE_")}
    e.update(
        {
            "HOME": str(home),
            "HIVE_DIR": str(home / "no-checkout"),
            "HIVE_GONEDARK_RESTART_CMD": f"touch {home / 'restarted'}",
            "HIVE_GONEDARK_STOP_CMD": f"touch {home / 'stopped'}",
        }
    )
    e.update(extra)
    return e


def setup(home: Path, *args: str, stdin: str = "", **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SETUP), *args],
        env=_env(home, **extra),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )


def check(home: Path, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(WRAPPER), "--check"],
        env=_env(home, **extra),
        capture_output=True,
        text=True,
        timeout=30,
    )


def conf(home: Path) -> Path:
    return home / ".config" / "hive" / "gonedark.env"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _no_address(*results: subprocess.CompletedProcess) -> None:
    for r in results:
        assert CANARY not in r.stdout and CANARY not in r.stderr
        assert "hc-ping" not in r.stdout and "hc-ping" not in r.stderr


def test_off_by_default(home: Path) -> None:
    r = check(home)
    assert r.returncode == 1 and "skipping: off: no ping address configured" in r.stderr
    s = setup(home, "status")
    assert s.returncode == 0 and "Off" in s.stderr


def test_set_writes_a_private_file_and_restarts_the_sender(home: Path) -> None:
    r = setup(home, "set", stdin=f"{URL}\n")
    assert r.returncode == 0, r.stderr
    f = conf(home)
    assert _mode(f) == 0o600 and _mode(f.parent) == 0o700
    text = f.read_text()
    assert f"HIVE_GONEDARK_URL={URL}\n" in text and "HIVE_GONEDARK_INTERVAL_S=300\n" in text
    assert (home / "restarted").exists()
    c = check(home)
    assert c.returncode == 0, c.stderr
    _no_address(r, c)


def test_every_sets_and_keeps_the_interval(home: Path) -> None:
    assert setup(home, "set", "--every", "120", stdin=f"{URL}\n").returncode == 0
    assert "HIVE_GONEDARK_INTERVAL_S=120\n" in conf(home).read_text()
    assert setup(home, "set", stdin=f"{URL}/2\n").returncode == 0  # rotate, interval kept
    assert "HIVE_GONEDARK_INTERVAL_S=120\n" in conf(home).read_text()
    r = setup(home, "set", "--every", "30", stdin=f"{URL}\n")
    assert r.returncode == 1 and "60 to 3600" in r.stderr


@pytest.mark.parametrize(
    "stdin",
    [
        "not-an-address\n",
        f"http://hc-ping.example/{CANARY}\n",
        f"ftp://hc-ping.example/{CANARY}\n",
        f"https://user:pw@hc-ping.example/{CANARY}\n",
        f"https://hc-ping.example/{CANARY}$(touch pwned)\n",
        f"https://hc-ping.example/{CANARY}'x\n",
        "\n",
    ],
)
def test_set_refuses_anything_but_https_and_saves_nothing(home: Path, stdin: str) -> None:
    r = setup(home, "set", stdin=stdin)
    assert r.returncode == 1 and "Nothing was saved" in r.stderr
    assert not conf(home).exists() and not (home / "restarted").exists()
    assert not (home / "pwned").exists()
    _no_address(r)


def test_status_never_shows_the_address(home: Path) -> None:
    setup(home, "set", stdin=f"{URL}\n")
    state = home / ".local" / "state" / "hive-gonedark"
    state.mkdir(parents=True)
    (state / "last.json").write_text(
        json.dumps(
            {
                "at": 1800000000.5,
                "result": "skipped",
                "reason": "the desk is not answering",
                "interval_s": 300,
            }
        )
    )
    r = setup(home, "status")
    assert r.returncode == 0
    assert "Ping address: set (hidden)" in r.stderr
    assert "skipped (the desk is not answering)" in r.stderr
    _no_address(r)


def test_remove_deletes_the_config_and_stops_the_sender(home: Path) -> None:
    setup(home, "set", stdin=f"{URL}\n")
    r = setup(home, "remove")
    assert r.returncode == 0, r.stderr
    assert not conf(home).exists() and (home / "stopped").exists()
    assert "pause or delete the check there" in r.stderr
    assert check(home).returncode == 1


def test_a_loose_config_is_skipped(home: Path) -> None:
    setup(home, "set", stdin=f"{URL}\n")
    conf(home).chmod(0o644)
    r = check(home)
    assert r.returncode == 1 and "readable by other users" in r.stderr
    _no_address(r)


@pytest.mark.parametrize(
    "line",
    [
        f"HIVE_GONEDARK_URL=http://hc-ping.example/{CANARY}",
        f"HIVE_GONEDARK_URL=https://hc-ping.example/{CANARY} extra",
        "HIVE_GONEDARK_URL=",
    ],
)
def test_a_malformed_config_is_skipped_without_printing_it(home: Path, line: str) -> None:
    f = conf(home)
    f.parent.mkdir(parents=True)
    f.write_text(line + "\n")
    f.chmod(0o600)
    r = check(home)
    assert r.returncode == 1 and "skipping" in r.stderr
    _no_address(r)


def test_a_bad_interval_is_skipped(home: Path) -> None:
    setup(home, "set", stdin=f"{URL}\n")
    r = check(home, HIVE_GONEDARK_INTERVAL_S="5")
    assert r.returncode == 1 and "60 to 3600" in r.stderr


def test_the_config_file_is_never_sourced(home: Path) -> None:
    f = conf(home)
    f.parent.mkdir(parents=True)
    f.write_text(f"HIVE_GONEDARK_URL=https://hc-ping.example/x\ntouch {home / 'pwned'}\n")
    f.chmod(0o600)
    assert check(home).returncode == 0
    assert not (home / "pwned").exists()
