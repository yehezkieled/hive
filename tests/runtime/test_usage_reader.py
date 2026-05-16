"""Tests for reading per-turn token usage from Claude Code session .jsonl files."""

import json
from pathlib import Path

import pytest

from hive.runtime.usage_reader import read_last_usage


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines))


def test_reads_usage_from_last_assistant_turn(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    log = session_dir / "session.jsonl"
    _write_jsonl(log, [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "world",
            "usage": {"input_tokens": 42, "output_tokens": 17},
        },
    ])

    usage = read_last_usage(session_dir)

    assert usage["input_tokens"] == 42
    assert usage["output_tokens"] == 17


def test_returns_zeros_when_no_jsonl_file(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    usage = read_last_usage(empty_dir)
    assert usage == {"input_tokens": 0, "output_tokens": 0}


def test_returns_zeros_when_file_is_malformed(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "session.jsonl").write_text("not json at all\n{broken")
    usage = read_last_usage(session_dir)
    assert usage == {"input_tokens": 0, "output_tokens": 0}


def test_returns_zeros_when_no_assistant_turn_with_usage(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    log = session_dir / "session.jsonl"
    _write_jsonl(log, [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},  # no usage key
    ])
    usage = read_last_usage(session_dir)
    assert usage == {"input_tokens": 0, "output_tokens": 0}
