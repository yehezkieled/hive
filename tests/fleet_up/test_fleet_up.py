"""fleet-up.sh: dry-run and idempotency, with stubbed herdr/process/port checks.

Nothing here starts a real service: herdr is a stub, ports and the claude
process are faked through FLEET_UP_FAKE_* variables, and every real-start path
runs only with --only firstmate against the stub.
"""

import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "fleet-up.sh"
DEPLOY = ROOT / "deploy"

STUB_HERDR = r"""#!/usr/bin/env bash
# Stateful herdr stub: `workspace create` makes `pane list` report firstmate.
echo "$*" >> "$STUB_DIR/calls"
case "$1 $2" in
  "pane list")
    if [ -f "$STUB_DIR/created" ]; then
      printf '{"result":{"panes":[{"agent":"claude","cwd":"%s","pane_id":"w1:p1"}]}}' "$FM_DIR"
    else
      printf '{"result":{"panes":[]}}'
    fi ;;
  "workspace create")
    touch "$STUB_DIR/created"
    printf '{"result":{"workspace":{"pane_id":"w1:p1"}}}' ;;
  "pane run") ;;
  "status --json") printf '{"server":{"running":true}}' ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    herdr = tmp_path / "herdr"
    herdr.write_text(STUB_HERDR)
    herdr.chmod(herdr.stat().st_mode | stat.S_IEXEC)
    fm_dir = tmp_path / "firstmate"
    fm_dir.mkdir()
    e = {
        **os.environ,
        "HOME": str(tmp_path),
        "STUB_DIR": str(stub_dir),
        "HERDR_BIN": str(herdr),
        "FM_DIR": str(fm_dir),
        "FLEET_UP_FAKE_PORTS": "",
        "FLEET_UP_FAKE_FM_PROCESS": "0",
        "TELEGRAM_STATUS_CMD": "false",
        "TAILSCALE_BIN": str(tmp_path / "no-such-tailscale"),
    }
    return e, stub_dir


def run(env, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60
    )


def calls(stub_dir):
    f = stub_dir / "calls"
    return f.read_text().splitlines() if f.exists() else []


def test_dry_run_prints_every_step_and_executes_nothing(env):
    e, stub = env
    r = run(e, "--dry-run")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for port in ("8480", "3001", "4388", "8765"):
        assert f":{port} closed; would run" in out
    for pair in ("8443", "8444", "8445", "8446"):
        assert f"serve --bg --https={pair}" in out
    assert "telegram: not running; would run: systemctl --user start hive-telegram" in out
    assert "would run: " in out and "workspace create" in out
    assert not any(c.startswith(("workspace create", "pane run")) for c in calls(stub))
    assert "funnel" not in out


def test_open_ports_are_left_alone(env):
    e, _ = env
    e["FLEET_UP_FAKE_PORTS"] = "8480 3001 4388 8765"
    r = run(e, "--dry-run", "--only", "gateway,bnm,lavish,preview")
    assert r.stdout.count("already listening") == 4
    assert "would run" not in r.stdout


def test_firstmate_pane_running_is_not_restarted(env):
    e, stub = env
    (stub / "created").touch()
    r = run(e, "--only", "firstmate")
    assert r.returncode == 0
    assert "already running in a herdr pane" in r.stdout
    assert not any(c.startswith("workspace create") for c in calls(stub))


def test_firstmate_process_running_is_not_restarted(env):
    e, stub = env
    e["FLEET_UP_FAKE_FM_PROCESS"] = "1"
    r = run(e, "--only", "firstmate")
    assert "not starting a second" in r.stdout
    assert not any(c.startswith("workspace create") for c in calls(stub))


def test_firstmate_starts_exactly_once_across_runs(env):
    e, stub = env
    first = run(e, "--only", "firstmate")
    assert first.returncode == 0, first.stdout + first.stderr
    second = run(e, "--only", "firstmate")
    assert second.returncode == 0
    c = calls(stub)
    assert sum(x.startswith("workspace create") for x in c) == 1
    pane_runs = [x for x in c if x.startswith("pane run")]
    assert pane_runs == ["pane run w1:p1 claude"]


def test_env_overrides_config_file(env):
    e, _ = env
    e["GATEWAY_PORT"] = "9999"
    r = run(e, "--dry-run", "--only", "gateway")
    assert ":9999 closed" in r.stdout


def test_config_is_the_only_place_for_defaults():
    conf = (DEPLOY / "fleet-up" / "fleet-up.conf").read_text()
    for needle in ("8480", "3001", "4388", "8765", "8443=8765", "/firstmate"):
        assert needle in conf
    script = SCRIPT.read_text()
    code = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("funnel" in ln for ln in code)
    assert "/home/hezki" not in script


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_script_is_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="no systemd-analyze")
def test_systemd_units_verify():
    units = [str(p) for p in sorted((DEPLOY / "systemd").glob("hive-*"))]
    r = subprocess.run(
        ["systemd-analyze", "--user", "verify", *units], capture_output=True, text=True
    )
    # The ExecStart paths live in the installed checkout, absent in CI.
    problems = [
        ln for ln in (r.stdout + r.stderr).splitlines() if ln and "is not executable" not in ln
    ]
    assert problems == []


def test_gateway_unit_restarts_always():
    unit = (DEPLOY / "systemd" / "hive-gateway.service").read_text()
    assert "Restart=always" in unit
    assert "WantedBy=default.target" in unit


def test_telegram_step_skipped_when_already_running(env):
    e, _ = env
    e["TELEGRAM_STATUS_CMD"] = "true"
    r = run(e, "--dry-run", "--only", "telegram")
    assert "telegram: already running" in r.stdout


def test_telegram_failure_never_fails_fleet_up(env):
    e, _ = env
    e["TELEGRAM_START_CMD"] = "false"
    r = run(e, "--only", "telegram")
    assert r.returncode == 0
    assert "start failed (ignored)" in r.stdout


def _telegram(tmp_path, *args, **extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
    e.update(HOME=str(tmp_path), **extra)
    script = ROOT / "scripts" / "hive-telegram.sh"
    return subprocess.run(["bash", str(script), *args], env=e, capture_output=True, text=True)


def test_telegram_skips_without_config(tmp_path):
    r = _telegram(tmp_path, "--check")
    assert r.returncode == 1 and "skipping" in r.stderr
    r = _telegram(tmp_path)  # run mode: skip is a clean exit, so no restart loop
    assert r.returncode == 0 and "skipping" in r.stderr


def test_telegram_needs_both_token_and_allowlist(tmp_path):
    r = _telegram(tmp_path, "--check", TELEGRAM_BOT_TOKEN="x")
    assert r.returncode == 1


def test_telegram_check_passes_from_env_file(tmp_path):
    cfg = tmp_path / ".config" / "hive"
    cfg.mkdir(parents=True)
    (cfg / "telegram.env").write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_ALLOWED_USER_IDS=1\n")
    assert _telegram(tmp_path, "--check").returncode == 0


def test_telegram_units_are_independent_and_supervised():
    unit = (DEPLOY / "systemd" / "hive-telegram.service").read_text()
    assert "Restart=always" in unit and "ExecCondition=" in unit
    assert "Requires=" not in unit and "BindsTo=" not in unit
    gateway = (DEPLOY / "systemd" / "hive-gateway.service").read_text()
    assert "telegram" not in gateway
    plist = plistlib.loads((DEPLOY / "macos" / "com.hive.telegram.plist").read_bytes())
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert "TELEGRAM_BOT_TOKEN" not in (DEPLOY / "macos" / "com.hive.telegram.plist").read_text()


@pytest.mark.parametrize("name", ["com.hive.gateway.plist", "com.hive.fleet-up.plist"])
def test_plists_are_valid(name):
    data = plistlib.loads((DEPLOY / "macos" / name).read_bytes())
    assert data["RunAtLoad"] is True
    if name == "com.hive.gateway.plist":
        assert data["KeepAlive"] is True


def test_windows_script_has_dry_run_and_uninstall():
    ps = (DEPLOY / "windows" / "Register-HiveWsl.ps1").read_text()
    assert "[switch]$DryRun" in ps and "[switch]$Uninstall" in ps
    assert "AtStartup" in ps and "AtLogOn" in ps
