"""scripts/telegram-setup.sh and the config checks in scripts/hive-telegram.sh.

Everything runs against a temporary HOME; the restart and stop commands are stubs that
record they ran, so no service is touched and Telegram is never contacted.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.fleet_up.test_fleet_up import FAKE_TOKEN, NO_RUNTIME, OTHER_TOKEN, ROOT

SETUP = ROOT / "scripts" / "telegram-setup.sh"
WRAPPER = ROOT / "scripts" / "hive-telegram.sh"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path


def _env(home: Path, **extra: str) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM_", "HIVE_"))}
    e.update(
        {
            "HOME": str(home),
            "HIVE_DIR": str(home / "no-checkout"),
            "HIVE_RUNTIME_PATTERN": NO_RUNTIME,
            "HIVE_TELEGRAM_RESTART_CMD": f"touch {home / 'restarted'}",
            "HIVE_TELEGRAM_STOP_CMD": f"touch {home / 'stopped'}",
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
    return home / ".config" / "hive" / "telegram.env"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _no_token(*results: subprocess.CompletedProcess) -> None:
    for r in results:
        for tok in (FAKE_TOKEN, OTHER_TOKEN):
            assert tok not in r.stdout and tok not in r.stderr


# ---- helper ------------------------------------------------------------------------------


def test_set_writes_a_private_file_and_restarts_the_bot(home: Path) -> None:
    r = setup(home, "set", stdin=f"{FAKE_TOKEN}\n42\n")
    assert r.returncode == 0, r.stderr
    f = conf(home)
    assert _mode(f) == 0o600 and _mode(f.parent) == 0o700
    text = f.read_text()
    assert f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n" in text
    assert "TELEGRAM_ALLOWED_USER_IDS=42\n" in text
    assert (home / "restarted").exists()
    assert "Allowed id(s): 42" in r.stderr
    _no_token(r)
    assert check(home).returncode == 0


def test_set_with_user_id_flag(home: Path) -> None:
    r = setup(home, "set", "--user-id", "42,7", stdin=f"{FAKE_TOKEN}\n")
    assert r.returncode == 0, r.stderr
    assert "TELEGRAM_ALLOWED_USER_IDS=42,7\n" in conf(home).read_text()


@pytest.mark.parametrize(
    "stdin",
    [
        "not-a-token\n42\n",
        f"{FAKE_TOKEN}$(touch pwned)\n42\n",
        f"{FAKE_TOKEN}\n@me\n",
        f"{FAKE_TOKEN}\n0\n",
        f"{FAKE_TOKEN}\n000\n",
        f"{FAKE_TOKEN}\n42,0\n",
        f"{FAKE_TOKEN}\n\n",
    ],
)
def test_set_rejects_malformed_input_and_saves_nothing(home: Path, stdin: str) -> None:
    r = setup(home, "set", stdin=stdin)
    assert r.returncode == 1 and "Nothing was saved" in r.stderr
    assert not conf(home).exists() and not (home / "restarted").exists()
    assert not (home / "pwned").exists()
    _no_token(r)


def test_rotate_is_one_step_and_keeps_the_allowlist(home: Path) -> None:
    setup(home, "set", stdin=f"{FAKE_TOKEN}\n42\n")
    (home / "restarted").unlink()
    r = setup(home, "set", stdin=f"{OTHER_TOKEN}\n\n")
    assert r.returncode == 0, r.stderr
    text = conf(home).read_text()
    assert OTHER_TOKEN in text and FAKE_TOKEN not in text
    assert "TELEGRAM_ALLOWED_USER_IDS=42\n" in text
    assert _mode(conf(home)) == 0o600
    assert (home / "restarted").exists()
    assert not list(conf(home).parent.glob(".telegram.env.*"))  # no temp file left
    _no_token(r)


def test_rotating_clears_a_rejected_mark(home: Path) -> None:
    setup(home, "set", stdin=f"{FAKE_TOKEN}\n42\n")
    state = home / ".local" / "state" / "hive-telegram"
    state.mkdir(parents=True)
    digest = hashlib.sha256(FAKE_TOKEN.encode()).hexdigest()
    (state / "rejected-token.sha256").write_text(digest + "\n")
    r = check(home)
    assert r.returncode == 1 and "rejected this bot token" in r.stderr
    setup(home, "set", stdin=f"{OTHER_TOKEN}\n\n")
    assert not (state / "rejected-token.sha256").exists()
    assert check(home).returncode == 0


def test_remove_is_one_step(home: Path) -> None:
    setup(home, "set", stdin=f"{FAKE_TOKEN}\n42\n")
    r = setup(home, "remove")
    assert r.returncode == 0
    assert not conf(home).exists() and (home / "stopped").exists()
    assert "revoke" in r.stderr
    r = check(home)
    assert r.returncode == 1 and "must both be set" in r.stderr


def test_remove_deletes_config_but_reports_a_failed_stop(home: Path) -> None:
    setup(home, "set", stdin=f"{FAKE_TOKEN}\n42\n")
    system_bin = home / "bin"
    system_bin.mkdir()
    systemctl = system_bin / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n")
    systemctl.chmod(0o755)
    env_path = f"{system_bin}:{os.environ['PATH']}"

    r = setup(
        home,
        "remove",
        HIVE_TELEGRAM_STOP_CMD="false",
        PATH=env_path,
    )

    assert r.returncode == 1
    assert not conf(home).exists()
    assert "Removed" in r.stderr
    assert "Could not stop the bot" in r.stderr and "may keep answering" in r.stderr
    assert "systemctl --user stop hive-telegram.service" in r.stderr
    assert "token is revoked in BotFather" in r.stderr
    assert "The bot is stopped" not in r.stderr


def test_macos_set_loads_agent_and_remove_boots_it_out(home: Path) -> None:
    bin_dir = home / "bin"
    bin_dir.mkdir()
    uname = bin_dir / "uname"
    uname.write_text("#!/bin/sh\nprintf 'Darwin\\n'\n")
    uname.chmod(0o755)
    launchctl = bin_dir / "launchctl"
    launchctl.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$LAUNCHCTL_LOG"\n'
        'case "$1" in print) exit 1 ;; bootstrap|kickstart|bootout) exit 0 ;; esac\n'
        "exit 1\n"
    )
    launchctl.chmod(0o755)
    log = home / "launchctl.log"
    extra = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LAUNCHCTL_LOG": str(log),
        "HIVE_TELEGRAM_RESTART_CMD": "",
        "HIVE_TELEGRAM_STOP_CMD": "",
    }

    configured = setup(home, "set", stdin=f"{FAKE_TOKEN}\n42\n", **extra)
    removed = setup(home, "remove", **extra)

    assert configured.returncode == 0, configured.stderr
    assert removed.returncode == 0, removed.stderr
    calls = log.read_text().splitlines()
    assert any(call.startswith("bootstrap gui/") and call.endswith("com.hive.telegram.plist") for call in calls)
    assert any(call.startswith("kickstart -k gui/") for call in calls)
    assert any(call.startswith("bootout gui/") for call in calls)
    assert all("kill TERM" not in call for call in calls)


def test_status_never_prints_the_token(home: Path) -> None:
    r = setup(home, "status")
    assert r.returncode == 0 and "Not configured" in r.stderr
    setup(home, "set", stdin=f"{FAKE_TOKEN}\n42\n")
    r = setup(home, "status")
    assert "Token: set (hidden)" in r.stderr and "mode 600" in r.stderr
    _no_token(r)


def test_restart_failure_is_explained_not_fatal(home: Path) -> None:
    r = setup(home, "set", stdin=f"{FAKE_TOKEN}\n42\n", HIVE_TELEGRAM_RESTART_CMD="false")
    assert r.returncode == 0 and "fleet-up starts it" in r.stderr


# ---- wrapper checks ----------------------------------------------------------------------


def _write(home: Path, text: str, mode: int = 0o600) -> None:
    f = conf(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    f.chmod(mode)


def test_wrapper_refuses_a_file_others_can_read(home: Path) -> None:
    _write(home, f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_ALLOWED_USER_IDS=42\n", 0o644)
    r = check(home)
    assert r.returncode == 1 and "readable by other users" in r.stderr
    _no_token(r)


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_ALLOWED_USER_IDS=42\n", "BotFather"),
        (f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_ALLOWED_USER_IDS=me\n", "numeric"),
        (f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_ALLOWED_USER_IDS=0\n", "not contain 0"),
        (
            f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_ALLOWED_USER_IDS=42,0\n",
            "not contain 0",
        ),
        (
            f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_ALLOWED_USER_IDS=42,000\n",
            "not contain 0",
        ),
        (f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n", "must both be set"),
    ],
)
def test_wrapper_skips_a_malformed_config_without_echoing_it(
    home: Path, text: str, why: str
) -> None:
    _write(home, text)
    r = check(home)
    assert r.returncode == 1 and why in r.stderr
    _no_token(r)
    assert "abc" not in r.stderr


def test_wrapper_never_executes_the_config(home: Path) -> None:
    _write(home, f"touch {home / 'pwned'}\nTELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n")
    check(home)
    assert not (home / "pwned").exists()


def test_wrapper_run_mode_skip_is_a_clean_exit(home: Path) -> None:
    r = subprocess.run(
        ["bash", str(WRAPPER)], env=_env(home), capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0 and "skipping" in r.stderr
