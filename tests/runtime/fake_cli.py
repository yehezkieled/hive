"""A scriptable fake agent CLI for headless-adapter tests.

``make_fake_cli`` writes an executable that records its argv/stdin/env to a JSON
file and replays canned stdout/stderr/exit code — so the adapters' real
subprocess path (spawn, stdin, parse, classify) runs with no real harness, no
network, and no spend.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path


def make_fake_cli(
    tmp_path: Path,
    name: str,
    *,
    stdout: str = "",
    stderr: str = "",
    rc: int = 0,
    sleep: float = 0.0,
) -> Path:
    path = tmp_path / name
    log = tmp_path / f"{name}.calls.json"
    path.write_text(
        f"""#!{sys.executable}
import json, os, sys, time
calls = []
try:
    calls = json.load(open({str(log)!r}))
except Exception:
    pass
calls.append({{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "env": dict(os.environ),
               "cwd": os.getcwd()}})
json.dump(calls, open({str(log)!r}, "w"))
time.sleep({sleep!r})
sys.stdout.write({stdout!r})
sys.stderr.write({stderr!r})
sys.exit({rc!r})
"""
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def calls(tmp_path: Path, name: str) -> list[dict]:
    return json.loads((tmp_path / f"{name}.calls.json").read_text())


def jsonl(*events: dict) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)
