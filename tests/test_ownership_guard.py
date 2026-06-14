"""Tests for the PreToolUse ownership guard (Ticket 024, Slice B).

The guard runs as ``python3 -m hive.hooks.ownership_guard``: it reads a
CC tool-call JSON on stdin, reads ``HIVE_WRITE_ALLOW`` /
``HIVE_WRITE_DENY`` from the env, and exits 0 (allow) or 2 (block).
"""

from __future__ import annotations

import io
import json

import pytest

from hive.hooks.ownership_guard import decide, main


def test_decide_allows_path_under_allow_root() -> None:
    """A write under the allow-root returns (0, '')."""
    code, reason = decide(
        {"file_path": "/p/acme/src/main.py"},
        allow_root="/p/acme",
        deny_roots=[],
    )
    assert code == 0
    assert reason == ""


def test_decide_blocks_path_outside_allow_root() -> None:
    """A write outside the allow-root returns (2, <non-empty reason>)."""
    code, reason = decide(
        {"file_path": "/p/other/x.py"},
        allow_root="/p/acme",
        deny_roots=[],
    )
    assert code == 2
    assert reason != ""


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdin_json: dict,
    env: dict[str, str],
) -> int:
    """Drive main() with a fake stdin payload and a clean env slice."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_json)))
    monkeypatch.delenv("HIVE_WRITE_ALLOW", raising=False)
    monkeypatch.delenv("HIVE_WRITE_DENY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return main()


def test_main_allows_write_under_allow_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() returns 0 when the env-allow-root contains the target."""
    code = _run_main(
        monkeypatch,
        stdin_json={"tool_input": {"file_path": "/p/acme/src/a.py"}},
        env={"HIVE_WRITE_ALLOW": "/p/acme"},
    )
    assert code == 0


def test_main_blocks_write_outside_allow_root(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() returns 2 and prints a reason to stderr when blocked."""
    code = _run_main(
        monkeypatch,
        stdin_json={"tool_input": {"file_path": "/p/other/x.py"}},
        env={"HIVE_WRITE_ALLOW": "/p/acme"},
    )
    assert code == 2
    assert capsys.readouterr().err.strip() != ""


def test_main_blocks_write_under_deny_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() honours a ':'-split HIVE_WRITE_DENY and blocks owned roots."""
    code = _run_main(
        monkeypatch,
        stdin_json={"tool_input": {"file_path": "/p/beta/x.py"}},
        env={"HIVE_WRITE_DENY": "/p/acme:/p/beta"},
    )
    assert code == 2


def test_decide_fails_open_on_missing_file_path() -> None:
    """No file_path in tool_input → allow (0, '')."""
    code, reason = decide({}, allow_root="/p/acme", deny_roots=[])
    assert code == 0
    assert reason == ""


def test_decide_fails_open_on_empty_policy() -> None:
    """Neither allow nor deny set → allow even a real path (0, '')."""
    code, reason = decide(
        {"file_path": "/anywhere/x.py"},
        allow_root=None,
        deny_roots=[],
    )
    assert code == 0
    assert reason == ""


def test_main_fails_open_when_target_outside_but_no_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() with a real path but no env policy returns 0 (fail open)."""
    code = _run_main(
        monkeypatch,
        stdin_json={"tool_input": {"file_path": "/p/anywhere/x.py"}},
        env={},
    )
    assert code == 0
