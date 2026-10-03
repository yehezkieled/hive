"""Codex subprocess protocol without spending a model request."""

from __future__ import annotations

import pytest

import hive.config as config
from hive.runtime.adapter_config import AdapterConfig
from hive.runtime.codex_adapter import CodexAdapter, probe_codex
from hive.runtime.harness import (
    HarnessDetector,
    HarnessError,
    HarnessSpec,
    HarnessStatus,
    RuntimeContext,
)
from hive.runtime.harness import HarnessErrorKind as K
from hive.runtime.harness_runtime import HarnessRuntime
from hive.runtime.registry import default_specs
from tests.runtime.fake_cli import calls, jsonl, make_fake_cli

THREAD = {"type": "thread.started", "thread_id": "codex-thread-1"}
MESSAGE = {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}
DONE = {
    "type": "turn.completed",
    "usage": {
        "input_tokens": 120,
        "cached_input_tokens": 40,
        "cache_write_input_tokens": 8,
        "output_tokens": 12,
    },
}


def _adapter(tmp_path, monkeypatch, *, stdout="", stderr="", rc=0, session=None):
    monkeypatch.setattr(
        config,
        "CODEX_BINARY",
        str(make_fake_cli(tmp_path, "codex", stdout=stdout, stderr=stderr, rc=rc)),
    )
    return CodexAdapter(
        AdapterConfig(name="n", role="lead", system_prompt="Be precise", permission_mode="yotree"),
        tmp_path,
        session,
    )


async def test_success_model_effort_and_usage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-codex")
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(THREAD, MESSAGE, DONE))
    monkeypatch.setattr(config, "CODEX_MODEL", "gpt-6.1-sol")
    monkeypatch.setattr(config, "CODEX_EFFORT", "medium")
    text, usage = await a.send_turn("say hi")
    assert text == "done"
    assert usage == {
        "input_tokens": 120,
        "output_tokens": 12,
        "cache_read_input_tokens": 40,
        "cache_creation_input_tokens": 8,
        "session_id": "codex-thread-1",
        "model": "gpt-6.1-sol",
        "cost_usd": None,
    }
    call = calls(tmp_path, "codex")[0]
    assert call["argv"][:2] == ["exec", "--json"]
    assert call["argv"][call["argv"].index("--model") + 1] == "gpt-6.1-sol"
    assert 'model_reasoning_effort="medium"' in call["argv"]
    assert "--dangerously-bypass-approvals-and-sandbox" in call["argv"]
    assert "Be precise" in call["stdin"] and call["stdin"].endswith("say hi")
    assert call["cwd"] == str(tmp_path)
    assert "OPENAI_API_KEY" not in call["env"]


async def test_resume_uses_thread_from_previous_turn(tmp_path, monkeypatch) -> None:
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(THREAD, MESSAGE, DONE))
    await a.send_turn("one")
    await a.send_turn("two")
    assert calls(tmp_path, "codex")[1]["argv"][:4] == ["exec", "resume", "codex-thread-1", "--json"]


async def test_role_override_sets_model_and_effort(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HIVE_CODEX_MODEL_LEAD", "custom-sol")
    monkeypatch.setenv("HIVE_CODEX_EFFORT_LEAD", "high")
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(THREAD, MESSAGE, DONE))
    _, usage = await a.send_turn("x")
    argv = calls(tmp_path, "codex")[0]["argv"]
    assert argv[argv.index("--model") + 1] == "custom-sol"
    assert 'model_reasoning_effort="high"' in argv
    assert usage["model"] == "custom-sol"


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("You've hit your usage limit", K.QUOTA),
        ("Rate limit exceeded (429)", K.QUOTA),
        ("Not logged in. Run codex login", K.AUTH),
        ("provider connection closed", K.OTHER),
    ],
)
async def test_turn_failure_classification(tmp_path, monkeypatch, message, kind) -> None:
    failed = {"type": "turn.failed", "error": {"message": message}}
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(THREAD, failed), rc=1)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is kind


async def test_failure_after_tool_activity_never_falls_back(tmp_path, monkeypatch) -> None:
    tool = {"type": "item.started", "item": {"type": "command_execution", "command": "touch x"}}
    failed = {"type": "turn.failed", "error": {"message": "429 rate limit exceeded"}}
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(THREAD, tool, failed), rc=1)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.OTHER and not ei.value.falls_back


async def test_error_event_preceding_empty_turn_failure_is_used(tmp_path, monkeypatch) -> None:
    a = _adapter(
        tmp_path,
        monkeypatch,
        stdout=jsonl(
            THREAD,
            {"type": "error", "error": {"message": "429 rate limit exceeded"}},
            {"type": "turn.failed", "error": {}},
        ),
        rc=1,
    )
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.QUOTA


async def test_no_result_uses_stderr_only(tmp_path, monkeypatch) -> None:
    a = _adapter(tmp_path, monkeypatch, stdout="quota exceeded", stderr="network broke", rc=1)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.OTHER


def test_probe_uses_login_status_without_model_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "CODEX_BINARY",
        str(make_fake_cli(tmp_path, "codex", stdout="Logged in using ChatGPT\n")),
    )
    status = probe_codex()
    assert status.installed and status.signed_in and status.usable
    assert calls(tmp_path, "codex")[0]["argv"] == ["login", "status"]


def test_probe_signed_out_and_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        config, "CODEX_BINARY", str(make_fake_cli(tmp_path, "codex", stderr="Not logged in", rc=1))
    )
    assert probe_codex().signed_in is False
    monkeypatch.setattr(config, "CODEX_BINARY", str(tmp_path / "missing"))
    assert not probe_codex().installed


def test_probe_rejects_api_key_login(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "CODEX_BINARY",
        str(make_fake_cli(tmp_path, "codex", stdout="Logged in using API key\n")),
    )
    assert probe_codex().signed_in is False


@pytest.mark.parametrize("claude_ready", [True, False])
async def test_codex_quota_falls_to_claude_then_pi(tmp_path, monkeypatch, claude_ready) -> None:
    codex_failure = {"type": "turn.failed", "error": {"message": "You've hit your usage limit"}}
    monkeypatch.setattr(
        config,
        "CODEX_BINARY",
        str(make_fake_cli(tmp_path, "codex", stdout=jsonl(THREAD, codex_failure), rc=1)),
    )
    monkeypatch.setattr(
        config,
        "CLAUDE_BINARY",
        str(
            make_fake_cli(
                tmp_path,
                "claude",
                stdout=jsonl(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "from claude",
                        "session_id": "c1",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                ),
            )
        ),
    )
    pi_output = jsonl(
        {"type": "session", "id": "p1"},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "from pi"}],
                "usage": {"input": 1, "output": 1},
            },
        },
    )
    monkeypatch.setattr(config, "PI_BINARY", str(make_fake_cli(tmp_path, "pi", stdout=pi_output)))
    specs = default_specs()
    statuses = {name: HarnessStatus(name, True, name != "claude" or claude_ready) for name in specs}
    detector = HarnessDetector(
        {
            name: HarnessSpec(**{**spec.__dict__, "probe": lambda name=name: statuses[name]})
            for name, spec in specs.items()
        }
    )
    rt = HarnessRuntime(
        RuntimeContext(AdapterConfig(name="n", role="lead"), cwd=tmp_path),
        detector,
        harness_order=["codex", "claude", "pi"],
        mode_order=["headless"],
    )
    await rt.start()
    text, usage = await rt.send_turn("x")
    assert text == ("from claude" if claude_ready else "from pi")
    assert usage["harness"] == ("claude" if claude_ready else "pi")
    assert "codex (headless): quota" in usage["fell_back"][0]
    assert calls(tmp_path, "codex")
    await rt.stop()
