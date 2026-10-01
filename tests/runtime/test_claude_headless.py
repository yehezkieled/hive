"""ClaudeHeadlessAdapter against a fake ``claude`` executable (real subprocess path)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import hive.config as config
from hive.runtime.adapter_config import AdapterConfig
from hive.runtime.claude_headless import ClaudeHeadlessAdapter, probe_claude
from hive.runtime.harness import HarnessError
from hive.runtime.harness import HarnessErrorKind as K
from tests.runtime.fake_cli import calls, jsonl, make_fake_cli

_USAGE = {
    "input_tokens": 12,
    "output_tokens": 7,
    "cache_creation_input_tokens": 3,
    "cache_read_input_tokens": 100,
}


def _ok(text: str = "done", session: str = "sess-A") -> str:
    return jsonl(
        {"type": "system", "subtype": "init", "session_id": session},
        {"type": "result", "subtype": "success", "is_error": False, "result": text,
         "session_id": session, "usage": _USAGE, "total_cost_usd": 0.5},
    )  # fmt: skip


# Captured from `claude -p` with an empty CLAUDE_CONFIG_DIR (logged out), 2.1.286.
_LOGGED_OUT = jsonl(
    {"type": "system", "subtype": "init", "session_id": "s"},
    {"type": "assistant", "error": "authentication_failed", "is_api_error_message": True,
     "message": {"content": [{"type": "text", "text": "Not logged in · Please run /login"}]}},
    {"type": "result", "subtype": "success", "is_error": True, "terminal_reason": "api_error",
     "result": "Not logged in · Please run /login", "session_id": "s"},
)  # fmt: skip


def _adapter(tmp_path: Path, monkeypatch, **fake) -> ClaudeHeadlessAdapter:
    binary = make_fake_cli(tmp_path, "claude", **fake)
    monkeypatch.setattr(config, "CLAUDE_BINARY", str(binary))
    cfg = AdapterConfig(model="opus", name="otter", role="maestro", permission_mode="yolo")
    return ClaudeHeadlessAdapter(cfg, cwd=tmp_path)


async def test_success_returns_text_and_usage(tmp_path, monkeypatch) -> None:
    a = _adapter(tmp_path, monkeypatch, stdout=_ok("hello"))
    await a.start()

    text, usage = await a.send_turn("do the thing")

    assert text == "hello"
    assert usage["input_tokens"] == 12 and usage["output_tokens"] == 7
    assert usage["cache_read_input_tokens"] == 100
    assert usage["session_id"] == "sess-A"
    assert usage["cost_usd"] is None  # plan-billed
    call = calls(tmp_path, "claude")[0]
    assert call["stdin"] == "do the thing"  # prompt over stdin, not argv
    assert "-p" in call["argv"] and "stream-json" in call["argv"]
    assert call["argv"][call["argv"].index("--model") + 1] == "opus"
    assert "--dangerously-skip-permissions" in call["argv"]  # yolo
    assert call["cwd"] == str(tmp_path)


async def test_second_turn_resumes_the_session(tmp_path, monkeypatch) -> None:
    a = _adapter(tmp_path, monkeypatch, stdout=_ok(session="sess-A"))
    await a.send_turn("one")
    await a.send_turn("two")

    first, second = calls(tmp_path, "claude")
    assert "--resume" not in first["argv"]
    assert second["argv"][second["argv"].index("--resume") + 1] == "sess-A"


async def test_api_key_env_is_scrubbed(tmp_path, monkeypatch) -> None:
    """Headless must never silently bill a per-token API key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    a = _adapter(tmp_path, monkeypatch, stdout=_ok())
    await a.send_turn("x")

    env = calls(tmp_path, "claude")[0]["env"]
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env


async def test_logged_out_is_classified_auth(tmp_path, monkeypatch) -> None:
    a = _adapter(tmp_path, monkeypatch, stdout=_LOGGED_OUT, rc=1)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.AUTH and ei.value.falls_back
    assert "Not logged in" in ei.value.detail


@pytest.mark.parametrize(
    ("code", "text", "kind"),
    [
        ("rate_limit", "You've hit your limit", K.QUOTA),
        ("billing_error", "Credit balance too low", K.QUOTA),
        (None, "Headless usage is not included in your plan", K.REFUSED),
        (None, "model exploded", K.OTHER),
    ],
)
async def test_error_codes_and_text_are_classified(tmp_path, monkeypatch, code, text, kind) -> None:
    assistant = {"type": "assistant", "message": {"content": []}}
    if code:
        assistant["error"] = code
    out = jsonl(
        assistant,
        {"type": "result", "subtype": "success", "is_error": True, "result": text},
    )
    a = _adapter(tmp_path, monkeypatch, stdout=out, rc=1)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is kind
    assert ei.value.falls_back is (kind is not K.OTHER)


async def test_missing_session_starts_fresh_instead_of_failing(tmp_path, monkeypatch) -> None:
    # Real: `claude -p --resume <unknown>` -> error_during_execution + stderr line.
    gone = jsonl({"type": "result", "subtype": "error_during_execution", "is_error": True})
    binary = make_fake_cli(
        tmp_path, "claude", stdout=gone, stderr="No conversation found with session ID: x", rc=1
    )
    monkeypatch.setattr(config, "CLAUDE_BINARY", str(binary))
    a = ClaudeHeadlessAdapter(AdapterConfig(name="n"), cwd=tmp_path, resume_session_id="x")
    with pytest.raises(HarnessError):  # the fake keeps failing, but only after one fresh retry
        await a.send_turn("x")

    runs = calls(tmp_path, "claude")
    assert len(runs) == 2
    assert "--resume" in runs[0]["argv"] and "--resume" not in runs[1]["argv"]


async def test_adopt_session_follows_pty_conversation(tmp_path, monkeypatch) -> None:
    a = _adapter(tmp_path, monkeypatch, stdout=_ok(session="sess-A"))
    a.adopt_session("pty-session")
    await a.send_turn("x")
    argv = calls(tmp_path, "claude")[0]["argv"]
    assert argv[argv.index("--resume") + 1] == "pty-session"


async def test_no_result_event_falls_to_stderr_classification(tmp_path, monkeypatch) -> None:
    a = _adapter(tmp_path, monkeypatch, stdout="", stderr="Error: 401 unauthorized", rc=1)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.AUTH


async def test_missing_binary_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CLAUDE_BINARY", str(tmp_path / "nope"))
    a = ClaudeHeadlessAdapter(AdapterConfig(name="n"))
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.UNAVAILABLE


async def test_timeout_is_not_a_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "HEADLESS_TIMEOUT_S", 0.3)
    a = _adapter(tmp_path, monkeypatch, stdout=_ok(), sleep=5)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.OTHER and not ei.value.falls_back


# --- probe ------------------------------------------------------------------


def _probe(tmp_path, monkeypatch, **fake):
    monkeypatch.setattr(config, "CLAUDE_BINARY", str(make_fake_cli(tmp_path, "claude", **fake)))
    return probe_claude()


def test_probe_signed_in(tmp_path, monkeypatch) -> None:
    s = _probe(
        tmp_path, monkeypatch, stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"})
    )
    assert s.installed and s.signed_in is True and s.usable


def test_probe_logged_out_exits_1(tmp_path, monkeypatch) -> None:
    # Real `claude auth status` when logged out: JSON on stdout, exit 1.
    s = _probe(tmp_path, monkeypatch, stdout=json.dumps({"loggedIn": False}), rc=1)
    assert s.installed and s.signed_in is False and not s.usable
    assert "logged out" in s.detail


def test_probe_not_installed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "CLAUDE_BINARY", str(tmp_path / "missing"))
    s = probe_claude()
    assert not s.installed and not s.usable
