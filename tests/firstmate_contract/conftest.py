"""Throwaway two-home firstmate world for the contract test.

A primary home and a ``hive`` second-mate home are built under ``tmp_path`` and driven
with the real firstmate scripts from a pinned checkout (``HIVE_FIRSTMATE_ROOT``, fetched
by ``scripts/fetch-firstmate.sh``). Nothing outside ``tmp_path`` is read or written:
``HOME`` is redirected, every home path is an ``FM_HOME``/``FM_ROOT_OVERRIDE`` temp dir,
and tmux, treehouse, gh and friends are shadowed by inert stubs so no real session or
agent can start. The tests skip when the checkout or its tools are absent, unless
``HIVE_FIRSTMATE_REQUIRED=1`` (set in CI), where that is a failure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from hive.gateway.settings import GatewaySettings

MATE_ID = "hive"
TOOLS = ("tasks-axi", "jq", "flock")

_TMUX = """#!/usr/bin/env bash
case "${1:-}" in
  has-session|new-session|new-window|kill-window|send-keys) exit 0 ;;
  list-windows) echo "fm-hive"; exit 0 ;;
  display-message) case "$*" in *'#{cursor_y}'*) echo 0 ;; *) echo firstmate ;; esac; exit 0 ;;
  capture-pane) printf '❯\\n'; exit 0 ;;
esac
exit 1
"""
_INERT = "#!/usr/bin/env bash\nexit 0\n"


def _unavailable(reason: str) -> None:
    if os.environ.get("HIVE_FIRSTMATE_REQUIRED") == "1":
        pytest.fail(f"firstmate contract test cannot run: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def firstmate_root() -> Path:
    raw = os.environ.get("HIVE_FIRSTMATE_ROOT")
    if not raw:
        _unavailable("HIVE_FIRSTMATE_ROOT is not set (run scripts/fetch-firstmate.sh)")
    root = Path(raw).resolve()
    if not (root / "bin" / "fm-fleet-snapshot.sh").is_file():
        _unavailable(f"{root} is not a firstmate checkout")
    for tool in TOOLS:
        if shutil.which(tool) is None:
            _unavailable(f"{tool} not found on PATH")
    return root


@dataclass
class World:
    root: Path  # the firstmate checkout (scripts)
    base: Path  # the throwaway directory holding everything below
    main: Path
    mate: Path
    env: dict[str, str]

    def home(self, which: str) -> Path:
        return self.main if which == "main" else self.mate

    def run(
        self,
        which: str,
        script: str,
        *args: str,
        stdin: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a firstmate script as the given home (``main`` or ``mate``)."""
        env = {**self.env, "FM_HOME": str(self.home(which))}
        proc = subprocess.run(
            [str(self.root / "bin" / script), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            cwd=self.home(which),
            timeout=90,
            check=False,
        )
        if check and proc.returncode != 0:
            raise AssertionError(
                f"{script} {' '.join(args)} as {which} exited {proc.returncode}\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        return proc

    def tasks(self, which: str, *args: str) -> str:
        return self.run(which, "fm-tasks-axi.sh", *args).stdout

    def backlog(self, which: str) -> str:
        return (self.home(which) / "data" / "backlog.md").read_text()

    def refresh_mate(self) -> None:
        """Publish the mate's structured ledger, as a live mate does on each event."""
        self.run("mate", "fm-home-summary-refresh.sh")

    def settings(self, **kwargs: object) -> GatewaySettings:
        """Gateway settings pointed at the primary home, as the desk runs in production."""
        return GatewaySettings(fm_home=self.main, live=False, snapshot_ttl_s=0, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def world(firstmate_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    base = tmp_path.resolve()
    main, mate, fakebin, fake_home = (base / n for n in ("main", "mate", "fakebin", "home"))
    for d in (main / "data", main / "state", main / "bin", mate / "data", mate / "state"):
        d.mkdir(parents=True)
    fakebin.mkdir()
    fake_home.mkdir()
    (mate / "bin").mkdir()

    stubs = {"tmux": _TMUX} | {
        t: _INERT for t in ("treehouse", "no-mistakes", "gh", "gh-axi", "herdr")
    }
    for name, body in stubs.items():
        (fakebin / name).write_text(body)
        (fakebin / name).chmod(0o755)

    # The gateway runs <fm_home>/bin/<script>; scripts resolve their code root from
    # FM_ROOT_OVERRIDE, so the primary's bin/ can simply point at the pinned checkout.
    (main / "bin").rmdir()
    (main / "bin").symlink_to(firstmate_root / "bin")
    for home in (main, mate):
        (home / "AGENTS.md").write_text("# Firstmate (throwaway)\n")
        (home / ".tasks.toml").write_text((firstmate_root / ".tasks.toml").read_text())
        (home / "data" / "backlog.md").write_text("## In flight\n\n## Queued\n\n## Done\n")
    (mate / ".fm-secondmate-home").write_text(f"{MATE_ID}\n")
    (mate / ".fm-secondmate-parent").write_text(
        f"schema=fm-secondmate-parent.v1\nroute=local\nparent_home={main}\n"
    )
    (main / "data" / "secondmates.md").write_text(
        f"- {MATE_ID} - Hive work (home: {mate}; scope: hive; projects: hive; added 2026-07-09)\n"
    )
    (main / "state" / f"{MATE_ID}.meta").write_text(
        f"window=firstmate:fm-{MATE_ID}\nkind=secondmate\nharness=claude\nbackend=tmux\n"
        f"home={mate}\nworktree={mate}\n"
    )

    env = {
        "PATH": f"{fakebin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(fake_home),
        "FM_ROOT_OVERRIDE": str(firstmate_root),
        "FM_SEND_SETTLE": "0",
        "FM_SEND_SLEEP": "0",
        "FM_SEND_RETRIES": "1",
        "FM_GATE_REFUSE_BYPASS": "1",
        "FM_TEST_SEAM": "1",
        "LANG": "C.UTF-8",
    }
    # Gateway code spawns scripts with os.environ, so the same world applies there.
    stray = ("TMUX", "TMUX_PANE", "FM_TASK_ID", "FM_HOME", "FM_STATE_OVERRIDE", "FM_DATA_OVERRIDE")
    for key in stray:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    yield World(firstmate_root, base, main, mate, env)
