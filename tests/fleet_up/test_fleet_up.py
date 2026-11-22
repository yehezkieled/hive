"""fleet-up.sh: dry-run and idempotency, with stubbed herdr/process/port checks.

Nothing here starts a real service: herdr is a stub, ports and the claude
process are faked through FLEET_UP_FAKE_* variables, and every real-start path
runs only with --only firstmate against the stub. The service manager is a stub
too: ``systemctl`` and ``systemd-run`` are fakes first on PATH that fail, so no
real unit is asked and no real scope is made.
"""

import configparser
import os
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "fleet-up.sh"
DEPLOY = ROOT / "deploy"

STUB_HERDR = r"""#!/usr/bin/env bash
# Stateful herdr stub: `workspace create` makes a firstmate workspace with one
# pane; `pane run` makes that pane report the claude agent. Deleting
# $STUB_DIR/running simulates claude exiting (the shell pane stays).
echo "$*" >> "$STUB_DIR/calls"
case "$1 $2" in
  "pane list")
    if [ ! -f "$STUB_DIR/created" ]; then
      printf '{"result":{"panes":[]}}'
    else
      agent=null
      [ -f "$STUB_DIR/running" ] && agent='"claude"'
      crew=""
      [ -f "$STUB_DIR/crew" ] && crew="$(cat "$STUB_DIR/crew"),"
      fm='{"agent":%s,"cwd":"%s","pane_id":"w1:p1","workspace_id":"w1"}'
      printf "{\"result\":{\"panes\":[%s$fm]}}" "$crew" "$agent" "${STUB_FM_CWD:-$FM_DIR}"
    fi ;;
  "workspace list")
    if [ -f "$STUB_DIR/created" ]; then
      printf '{"result":{"workspaces":[{"label":"firstmate","workspace_id":"w1"}]}}'
    else
      printf '{"result":{"workspaces":[]}}'
    fi ;;
  "workspace create")
    touch "$STUB_DIR/created"
    printf '{"result":{"workspace":{"pane_id":"w1:p1"}}}' ;;
  "pane run") touch "$STUB_DIR/running" ;;
  # The server is up unless a test took it down; `herdr server` brings it up again.
  "server ") touch "$STUB_DIR/server-started" ;;
  "status --json")
    if [ -f "$STUB_DIR/server-down" ] && [ ! -f "$STUB_DIR/server-started" ]; then
      printf '{"server":{"running":false}}'
    else
      printf '{"server":{"running":true}}'
    fi ;;
esac
"""

# Matches nothing on the host, so a real Hive runtime here never leaks in.
NO_RUNTIME = "fleet-up-test-no-such-runtime-xyz$"
ALL_PORTS = "8480 8492 3001 4388 8765"  # every service already listening


def _fake(tmp_path, name, body):
    """A fake command in the directory that is first on the tests' PATH."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    cmd = bindir / name
    cmd.write_text(f"#!/usr/bin/env bash\n{body}\n")
    cmd.chmod(0o755)
    return cmd


@pytest.fixture
def env(tmp_path):
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    herdr = tmp_path / "herdr"
    herdr.write_text(STUB_HERDR)
    herdr.chmod(herdr.stat().st_mode | stat.S_IEXEC)
    fm_dir = tmp_path / "firstmate"
    fm_dir.mkdir()
    for tool in ("systemctl", "systemd-run"):
        _fake(tmp_path, tool, "exit 1")
    e = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM_", "HIVE_"))},
        "PATH": f"{tmp_path / 'fakebin'}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "STUB_DIR": str(stub_dir),
        "HERDR_BIN": str(herdr),
        "FM_DIR": str(fm_dir),
        "FLEET_UP_FAKE_PORTS": "",
        "FLEET_UP_FAKE_FM_PROCESS": "0",
        "RUNTIME_STATUS_CMD": "false",
        "GONEDARK_STATUS_CMD": "false",
        "HIVE_RUNTIME_PATTERN": NO_RUNTIME,
        "HIVE_DIR": str(tmp_path),
        "TAILSCALE_BIN": str(tmp_path / "no-such-tailscale"),
    }
    return e, stub_dir


# Shaped like a BotFather token, and obviously not one.
FAKE_TOKEN = "123456789:" + "FAKE" * 9
OTHER_TOKEN = "987654321:" + "ALSOFAKE" * 4


def configure_telegram(e):
    e["TELEGRAM_BOT_TOKEN"] = FAKE_TOKEN
    e["TELEGRAM_ALLOWED_USER_IDS"] = "1"


@pytest.fixture
def fake_runtime():
    """A process whose command line ends like a running Hive runtime's."""
    marker = f"fake-hive-runtime-{os.getpid()}"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "-m", marker])
    try:
        yield f" -m {marker}$", proc.pid
    finally:
        proc.kill()
        proc.wait()


def run(env, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60
    )


def calls(stub_dir):
    f = stub_dir / "calls"
    return f.read_text().splitlines() if f.exists() else []


def test_dry_run_prints_every_step_and_executes_nothing(env, tmp_path):
    e, stub = env
    configure_telegram(e)
    r = run(e, "--dry-run")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for port in ("8480", "8492", "3001", "4388", "8765"):
        assert f":{port} closed; would run" in out
    for pair in ("8443", "8444", "8445", "8446", "8448"):
        assert f"serve --bg --https={pair}" in out
    assert "runtime: not running; would run: systemctl --user start hive-telegram" in out
    for d in ("apps/broke-no-more", "fm-preview-proxy", "fm-preview"):
        assert f"(in {tmp_path}/{d})" in out
    assert "would run: " in out and "workspace create" in out
    assert not any(c.startswith(("workspace create", "pane run")) for c in calls(stub))
    assert "funnel" not in out


def test_open_ports_are_left_alone(env):
    e, _ = env
    e["FLEET_UP_FAKE_PORTS"] = "8480 8492 3001 4388 8765"
    r = run(e, "--dry-run", "--only", "gateway,clipdesk,bnm,lavish,preview")
    assert r.stdout.count("already listening") == 5
    assert "would run" not in r.stdout


def test_firstmate_pane_running_is_not_restarted(env):
    e, stub = env
    (stub / "created").touch()
    (stub / "running").touch()
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


def test_exited_firstmate_is_retried_in_its_pane_not_a_new_workspace(env):
    e, stub = env
    assert run(e, "--only", "firstmate").returncode == 0
    for _ in range(3):  # claude exits at once (no network) and the timer reruns
        (stub / "running").unlink()
        r = run(e, "--only", "firstmate")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "reusing pane w1:p1" in r.stdout
    c = calls(stub)
    assert sum(x.startswith("workspace create") for x in c) == 1
    assert [x for x in c if x.startswith("pane run")] == ["pane run w1:p1 claude"] * 4


@pytest.mark.parametrize(
    "crew",
    [
        '{"agent":"codex","cwd":"/wt/task","pane_id":"w1:p9","workspace_id":"w1"}',
        '{"agent":"pi","cwd":"FM","pane_id":"w1:p9","workspace_id":"w1"}',
        '{"agent":null,"cwd":"/wt/task","pane_id":"w1:p9","workspace_id":"w1"}',
        # a crew opened in FM_DIR: same cwd, but it runs claude in its own worktree
        '{"agent":"claude","cwd":"FM","foreground_cwd":"/wt/task","pane_id":"w1:p9",'
        '"workspace_id":"w1"}',
    ],
)
def test_exited_firstmate_never_reuses_a_crew_pane(env, crew):
    e, stub = env
    (stub / "created").touch()
    (stub / "crew").write_text(crew.replace('"FM"', f'"{e["FM_DIR"]}"'))
    r = run(e, "--only", "firstmate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert [x for x in calls(stub) if x.startswith("pane run")] == ["pane run w1:p1 claude"]


def test_firstmate_workspace_without_an_idle_fm_pane_gets_a_new_one(env):
    e, stub = env
    (stub / "created").touch()
    crew = '{"agent":"codex","cwd":"/wt/task","pane_id":"w1:p9","workspace_id":"w1"}'
    (stub / "crew").write_text(crew)
    e["STUB_FM_CWD"] = "/home/someone/elsewhere"  # an idle shell, but not at FM_DIR
    r = run(e, "--only", "firstmate")
    assert r.returncode == 0, r.stdout + r.stderr
    c = calls(stub)
    assert sum(x.startswith("workspace create") for x in c) == 1
    assert not any(x.startswith("pane run w1:p9") for x in c)


def _bin(tmp_path, *tools):
    """A PATH holding only the named tools, so nothing else on the host can be reached."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in tools:
        (bindir / tool).symlink_to(shutil.which(tool))
    return str(bindir)


# What the firstmate step and the herdr stub need; no herdr, as on a service manager's PATH.
SERVICE_TOOLS = ("bash", "cat", "dirname", "jq", "touch")


def _bin_without_setsid(tmp_path):
    """A PATH holding only what ensure_service needs, minus setsid (as on macOS)."""
    return _bin(tmp_path, "bash", "dirname", "mkdir", "nohup", "sleep", "touch")


def _older_conf(tmp_path, *gone):
    """The shipped config as a copy made before some settings existed: without those lines."""
    conf = tmp_path / "fleet-up.conf"
    shipped = (DEPLOY / "fleet-up" / "fleet-up.conf").read_text().splitlines()
    conf.write_text("\n".join(ln for ln in shipped if not ln.startswith(gone)) + "\n")
    return conf


def _wait_for(path):
    for _ in range(50):
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def test_a_config_without_an_optional_step_skips_it_and_brings_firstmate_up(env, tmp_path):
    # 22 Nov: the installed config was a copy made before the gone-dark step existed. Under
    # `set -u` the run ended at that step, every five minutes, and never reached firstmate.
    e, stub = env
    del e["GONEDARK_STATUS_CMD"]
    e["FLEET_UP_FAKE_PORTS"] = ALL_PORTS
    conf = _older_conf(tmp_path, "GONEDARK_")
    r = run(e, "--conf", str(conf))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "unbound variable" not in r.stdout + r.stderr
    gonedark = [ln for ln in r.stdout.splitlines() if ln.startswith("fleet-up: gonedark")]
    assert gonedark == [f"fleet-up: gonedark: skipped; GONEDARK_STATUS_CMD is not set in {conf}"]
    assert "broke-no-more: already listening on :3001" in r.stdout
    assert "firstmate: started in pane w1:p1" in r.stdout
    assert "pane run w1:p1 claude" in calls(stub)


@pytest.mark.parametrize(
    ("step", "gone", "setting"),
    [
        ("clipdesk", "CLIP_DESK_", "CLIP_DESK_PORT"),
        ("runtime", "RUNTIME_", "RUNTIME_STATUS_CMD"),
        ("bnm", "BNM_", "BNM_PORT"),
        ("lavish", "LAVISH_START_CMD", "LAVISH_START_CMD"),
        ("preview", "PREVIEW_", "PREVIEW_PORT"),
        ("serve", "SERVE_MAP", "SERVE_MAP"),
    ],
)
def test_every_optional_step_is_skipped_when_its_setting_is_missing(
    env, tmp_path, step, gone, setting
):
    e, stub = env
    e.pop(setting, None)
    conf = _older_conf(tmp_path, gone)
    r = run(e, "--conf", str(conf), "--only", f"{step},firstmate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"fleet-up: {step}: skipped; {setting} is not set in {conf}" in r.stdout
    assert "failed" not in r.stdout
    assert "pane run w1:p1 claude" in calls(stub)


def test_a_step_that_dies_never_stops_the_steps_after_it(env, tmp_path):
    # a step that is not optional, missing its setting: it ends alone, the run goes on
    e, stub = env
    e["FLEET_UP_FAKE_PORTS"] = "3001"
    r = run(e, "--conf", str(_older_conf(tmp_path, "GATEWAY_")), "--only", "gateway,bnm,firstmate")
    assert r.returncode == 1
    assert "GATEWAY_PORT: unbound variable" in r.stderr
    assert "fleet-up: step gateway failed; the run goes on" in r.stdout
    assert "broke-no-more: already listening on :3001" in r.stdout
    assert "firstmate: started in pane w1:p1" in r.stdout
    assert "pane run w1:p1 claude" in calls(stub)


def test_a_service_that_does_not_come_up_never_stops_firstmate(env, tmp_path):
    e, stub = env
    e["PORT_WAIT"] = "1"
    e["BNM_DIR"] = str(tmp_path)
    e["BNM_START_CMD"] = "true"  # starts, and never listens
    r = run(e, "--only", "bnm,firstmate")
    assert r.returncode == 1
    assert "broke-no-more: still not listening on :3001 after 1s" in r.stdout
    assert "fleet-up: step bnm failed; the run goes on" in r.stdout
    assert "pane run w1:p1 claude" in calls(stub)


def test_firstmate_step_finds_herdr_in_the_users_own_bin_directory(env, tmp_path):
    # 22 Nov: the desk's Wake runs this script with the desk service's PATH, which has no
    # ~/.local/bin; herdr and claude are installed only there, so the start could not work.
    e, stub = env
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    shutil.copy(tmp_path / "herdr", local / "herdr")
    e["HERDR_BIN"] = "herdr"  # as the shipped config has it
    e["PATH"] = _bin(tmp_path, *SERVICE_TOOLS)
    r = run(e, "--only", "firstmate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "firstmate: started in pane w1:p1" in r.stdout
    assert "pane run w1:p1 claude" in calls(stub)


def test_firstmate_step_says_at_once_when_herdr_is_not_installed(env, tmp_path):
    e, stub = env
    e["HERDR_BIN"] = "herdr"
    e["PATH"] = _bin(tmp_path, *SERVICE_TOOLS)
    start = time.monotonic()
    r = run(e, "--only", "firstmate")
    assert r.returncode == 1
    assert "fleet-up: firstmate: herdr not found: 'herdr' is not a command here" in r.stdout
    assert "set HERDR_BIN to its full path in" in r.stdout
    assert time.monotonic() - start < 10  # no 20 s wait for a server that cannot start
    assert calls(stub) == []


def _scope_recorder(tmp_path):
    """A systemd-run that notes how it was called, then runs the command it was given."""
    log = tmp_path / "systemd-run.calls"
    _fake(
        tmp_path,
        "systemd-run",
        f'echo "$*" >> "{log}"\nwhile [ "$1" != -- ]; do shift; done\nshift\nexec "$@"',
    )
    return log


def test_the_herdr_server_is_started_in_a_scope_of_its_own(env, tmp_path):
    # The desk's Wake runs this script inside the desk's service. A plain child would stay
    # in that service's control group and be stopped (firstmate and every crew pane with
    # it) the next time the desk restarts; a scope of its own outlives it.
    e, stub = env
    log = _scope_recorder(tmp_path)
    (stub / "server-down").touch()
    r = run(e, "--only", "firstmate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "firstmate: starting the herdr server" in r.stdout
    assert "firstmate: started in pane w1:p1" in r.stdout
    scope = "--user --scope --quiet --collect --"
    assert log.read_text().splitlines() == [f"{scope} true", f"{scope} {tmp_path / 'herdr'} server"]


def test_the_herdr_server_starts_without_a_scope_when_no_user_manager_answers(env, tmp_path):
    e, stub = env  # the fixture's systemd-run fails, as where no user manager runs
    (stub / "server-down").touch()
    r = run(e, "--only", "firstmate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "server" in calls(stub)
    assert "firstmate: started in pane w1:p1" in r.stdout


def test_a_started_service_gets_no_scope(env, tmp_path):
    e, stub = env
    log = _scope_recorder(tmp_path)
    e["PORT_WAIT"] = "1"
    e["BNM_DIR"] = str(tmp_path)
    e["BNM_START_CMD"] = f"touch {stub}/bnm-started"
    run(e, "--only", "bnm")
    assert _wait_for(stub / "bnm-started")
    assert not log.exists()


def test_a_herdr_server_that_does_not_come_up_is_said_with_where_to_look(env, tmp_path):
    e, stub = env
    (stub / "server-down").touch()
    e["HERDR_SERVER_WAIT"] = "1"
    herdr = tmp_path / "herdr"
    herdr.write_text(herdr.read_text().replace('touch "$STUB_DIR/server-started"', "exit 1"))
    r = run(e, "--only", "firstmate")
    assert r.returncode == 1
    assert "firstmate: herdr server did not come up within 1s (see " in r.stdout
    assert f"{tmp_path}/.local/state/hive-fleet-up/herdr-server.log)" in r.stdout
    assert not any(c.startswith(("workspace create", "pane run")) for c in calls(stub))


def test_service_starts_without_setsid(env, tmp_path):
    e, stub = env
    e["PATH"] = _bin_without_setsid(tmp_path)
    e["PORT_WAIT"] = "1"
    e["BNM_DIR"] = str(tmp_path)
    e["BNM_START_CMD"] = f"touch {stub}/bnm-started"
    r = run(e, "--only", "bnm")
    assert "broke-no-more: starting on :3001" in r.stdout, r.stdout + r.stderr
    assert _wait_for(stub / "bnm-started")


def test_env_overrides_config_file(env):
    e, _ = env
    e["GATEWAY_PORT"] = "9999"
    r = run(e, "--dry-run", "--only", "gateway")
    assert ":9999 closed" in r.stdout


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
@pytest.mark.parametrize(
    "script",
    [
        "fleet-up.sh",
        "hive-telegram.sh",
        "telegram-setup.sh",
        "hive-gonedark.sh",
        "gonedark-setup.sh",
    ],
)
def test_script_is_shellcheck_clean(script):
    r = subprocess.run(
        ["shellcheck", str(ROOT / "scripts" / script)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="no systemd-analyze")
def test_systemd_units_verify():
    units = [str(p) for p in sorted((DEPLOY / "systemd").glob("*.service"))]
    r = subprocess.run(
        ["systemd-analyze", "--user", "verify", *units], capture_output=True, text=True
    )
    # The ExecStart paths live in the installed checkout, absent in CI.
    problems = [
        ln for ln in (r.stdout + r.stderr).splitlines() if ln and "is not executable" not in ln
    ]
    assert problems == []


def _unit(name):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(DEPLOY / "systemd" / name)
    return parser


def _environment(name):
    """The [Service] Environment= assignments, which systemd accumulates across lines."""
    env, section = {}, None
    for line in (DEPLOY / "systemd" / name).read_text().splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Service]" and line.startswith("Environment="):
            for pair in shlex.split(line.partition("=")[2]):
                k, _, v = pair.partition("=")
                env[k] = v
    return env


def _deps(unit):
    keys = ("Requires", "Requisite", "BindsTo", "PartOf", "Wants", "Upholds")
    return " ".join(unit["Unit"].get(k, "") for k in keys).split()


def test_gateway_unit_restarts_always():
    unit = _unit("hive-gateway.service")
    assert unit["Service"]["Restart"] == "always"
    assert unit["Install"]["WantedBy"] == "default.target"


def test_runtime_step_skipped_when_already_running(env):
    e, _ = env
    e["RUNTIME_STATUS_CMD"] = "true"
    r = run(e, "--dry-run", "--only", "runtime")
    assert "runtime: Telegram bot already running" in r.stdout


def test_runtime_step_skipped_when_unconfigured(env):
    e, _ = env
    r = run(e, "--dry-run", "--only", "runtime")
    assert r.returncode == 0
    assert "runtime: not starting; hive-telegram: skipping: TELEGRAM_BOT_TOKEN" in r.stdout
    assert "would run" not in r.stdout


def test_runtime_step_skipped_when_another_runtime_runs(env, fake_runtime):
    e, _ = env
    configure_telegram(e)
    e["HIVE_RUNTIME_PATTERN"], pid = fake_runtime
    r = run(e, "--dry-run", "--only", "runtime")
    assert r.returncode == 0
    assert f"a Hive runtime is already running (pid {pid})" in r.stdout
    assert "would run" not in r.stdout


def test_runtime_failure_never_fails_fleet_up(env):
    e, _ = env
    configure_telegram(e)
    e["RUNTIME_START_CMD"] = "false"
    r = run(e, "--only", "runtime")
    assert r.returncode == 0
    assert "start failed (ignored)" in r.stdout


def _telegram(tmp_path, *args, **extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM_", "HIVE_"))}
    base = {"HOME": str(tmp_path), "HIVE_DIR": str(tmp_path), "HIVE_RUNTIME_PATTERN": NO_RUNTIME}
    e.update({**base, **extra})
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
    body = f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_ALLOWED_USER_IDS=1\n"
    (cfg / "telegram.env").write_text(body)
    (cfg / "telegram.env").chmod(0o600)
    assert _telegram(tmp_path, "--check").returncode == 0


@pytest.mark.parametrize(
    "dotenv",
    [
        f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_ALLOWED_USER_IDS=1\n",
        f"# hive\nexport TELEGRAM_BOT_TOKEN='{FAKE_TOKEN}'\nTELEGRAM_ALLOWED_USER_IDS = \"1,2\"\n"
        "HIVE_DB=postgresql://u:p@h/db?a=1&b=2\n",
    ],
)
def test_telegram_check_passes_from_hive_dotenv(tmp_path, dotenv):
    hive_dir = tmp_path / "hive"
    hive_dir.mkdir()
    (hive_dir / ".env").write_text(dotenv)
    assert _telegram(tmp_path, "--check", HIVE_DIR=str(hive_dir)).returncode == 0


def test_telegram_dotenv_without_allowlist_is_unconfigured(tmp_path):
    hive_dir = tmp_path / "hive"
    hive_dir.mkdir()
    body = f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n# TELEGRAM_ALLOWED_USER_IDS=1\n"
    (hive_dir / ".env").write_text(body)
    r = _telegram(tmp_path, "--check", HIVE_DIR=str(hive_dir))
    assert r.returncode == 1 and "skipping" in r.stderr


def test_telegram_wrapper_never_starts_a_second_runtime(tmp_path, fake_runtime):
    pattern, pid = fake_runtime
    creds = {"TELEGRAM_BOT_TOKEN": FAKE_TOKEN, "TELEGRAM_ALLOWED_USER_IDS": "1"}
    r = _telegram(tmp_path, "--check", **creds, HIVE_RUNTIME_PATTERN=pattern)
    assert r.returncode == 1 and f"already running (pid {pid})" in r.stderr
    # run mode: a clean exit before exec, so launchd/systemd do not restart it
    r = _telegram(tmp_path, **creds, HIVE_RUNTIME_PATTERN=pattern)
    assert r.returncode == 0 and "already running" in r.stderr


def test_runtime_unit_conflicts_with_hive_service_and_is_independent():
    unit = _unit("hive-telegram.service")
    assert unit["Service"]["Restart"] == "always"
    assert unit["Service"]["ExecCondition"].endswith("hive-telegram.sh --check")
    assert unit["Unit"]["Conflicts"].split() == ["hive.service"]
    # A rejected token or bad config (exit 78) is not retried; the config lives in the
    # wrapper's checked file, not in a unit-level EnvironmentFile.
    assert unit["Service"]["RestartPreventExitStatus"] == "78"
    assert "EnvironmentFile" not in unit["Service"]
    assert unit["Install"]["WantedBy"] == "default.target"
    assert not any(d.startswith("hive-") for d in _deps(unit))
    assert not any(d.startswith("hive-") for d in _deps(_unit("hive-gateway.service")))
    plist = plistlib.loads((DEPLOY / "macos" / "com.hive.telegram.plist").read_bytes())
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert "TELEGRAM_BOT_TOKEN" not in plist.get("EnvironmentVariables", {})


def _configure_gonedark(tmp_path):
    f = tmp_path / ".config" / "hive" / "gonedark.env"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("HIVE_GONEDARK_URL=https://hc-ping.example/secret-check-id\n")
    f.chmod(0o600)


def test_gonedark_step_is_off_without_config(env):
    e, _ = env
    r = run(e, "--dry-run", "--only", "gonedark")
    assert r.returncode == 0
    assert "gonedark: not starting; hive-gonedark: skipping: off: no ping address" in r.stdout
    assert "would run" not in r.stdout


def test_gonedark_step_starts_the_sender_when_configured(env, tmp_path):
    e, _ = env
    _configure_gonedark(tmp_path)
    r = run(e, "--dry-run", "--only", "gonedark")
    assert "gonedark: not running; would run: systemctl --user start hive-gonedark.service" in (
        r.stdout
    )
    assert "secret-check-id" not in r.stdout + r.stderr
    e["GONEDARK_STATUS_CMD"] = "true"
    assert "gonedark: already running" in run(e, "--dry-run", "--only", "gonedark").stdout


def test_gonedark_failure_never_fails_fleet_up(env, tmp_path):
    e, _ = env
    _configure_gonedark(tmp_path)
    e["GONEDARK_START_CMD"] = "false"
    r = run(e, "--only", "gonedark")
    assert r.returncode == 0
    assert "gonedark: start failed (ignored)" in r.stdout


def test_gonedark_unit_is_optional_outbound_and_independent():
    unit = _unit("hive-gonedark.service")
    assert unit["Service"]["Restart"] == "always"
    assert unit["Service"]["ExecCondition"].endswith("hive-gonedark.sh --check")
    assert unit["Service"]["RestartPreventExitStatus"] == "78"
    assert "EnvironmentFile" not in unit["Service"]
    assert unit["Install"]["WantedBy"] == "default.target"
    assert not any(d.startswith("hive-") for d in _deps(unit))
    plist = plistlib.loads((DEPLOY / "macos" / "com.hive.gonedark.plist").read_bytes())
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ProgramArguments"][-1].endswith("scripts/hive-gonedark.sh")
    assert "HIVE_GONEDARK_URL" not in plist.get("EnvironmentVariables", {})


def test_dry_run_clip_desk_step_and_serve_pair(env):
    e, _ = env
    r = run(e, "--dry-run", "--only", "clipdesk,serve")
    assert "clipdesk: :8492 closed; would run: systemctl --user start clip-desk.service" in r.stdout
    assert "serve --bg --https=8448 http://127.0.0.1:8492" in r.stdout
    assert "8490" not in r.stdout


def test_clip_desk_units_use_8492_and_restart_always():
    unit = _unit("clip-desk.service")
    assert unit["Service"]["Restart"] == "always"
    assert _environment("clip-desk.service")["CLIP_DESK_PORT"] == "8492"
    plist = plistlib.loads((DEPLOY / "macos" / "com.hive.clip-desk.plist").read_bytes())
    assert plist["EnvironmentVariables"]["CLIP_DESK_PORT"] == "8492"
    assert plist["KeepAlive"] is True


@pytest.mark.parametrize(
    "name", ["com.hive.gateway.plist", "com.hive.clip-desk.plist", "com.hive.fleet-up.plist"]
)
def test_plists_are_valid(name):
    data = plistlib.loads((DEPLOY / "macos" / name).read_bytes())
    assert data["RunAtLoad"] is True
    if name != "com.hive.fleet-up.plist":
        assert data["KeepAlive"] is True
    else:
        assert data["AbandonProcessGroup"] is True
