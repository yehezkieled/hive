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
        "input_tokens": 80,
        "output_tokens": 12,
        "cache_read_input_tokens": 40,
        "cache_creation_input_tokens": 8,
        "session_id": "codex-thread-1",
        "model": "gpt-6.1-sol",
        "cost_usd": None,
        "codex_usage": {"session_id": "codex-thread-1", **DONE["usage"]},
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
        ("The model is not supported for this account", K.UNAVAILABLE),
        ("Model gpt-6.1-sol is not available", K.UNAVAILABLE),
    ],
)
async def test_turn_failure_classification(tmp_path, monkeypatch, message, kind) -> None:
    failed = {"type": "turn.failed", "error": {"message": message}}
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(THREAD, failed), rc=1)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is kind


@pytest.mark.parametrize("item_type", ["command_execution", "file_change", "mcp_tool_call", "collab_tool_call"])
@pytest.mark.parametrize("message", ["429 rate limit exceeded", "Model is not supported", "Thread not found"])
async def test_failure_after_tool_activity_never_falls_back(tmp_path, monkeypatch, item_type, message) -> None:
    tool = {"type": "item.started", "item": {"type": item_type}}
    failed = {"type": "turn.failed", "error": {"message": message}}
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(THREAD, tool, failed), rc=1, session="codex-thread-1")

    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.OTHER and not ei.value.falls_back
    assert len(calls(tmp_path, "codex")) == 1


async def test_error_event_preceding_empty_turn_failure_is_used(tmp_path, monkeypatch) -> None:
    a = _adapter(
        tmp_path,
        monkeypatch,
        stdout=jsonl(
            THREAD,
            {"type": "error", "message": "429 rate limit exceeded"},
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


@pytest.mark.parametrize("failure, kind", [("You've hit your usage limit", "quota"), ("The model is not supported", "unavailable")])
@pytest.mark.parametrize("claude_ready", [True, False])
async def test_codex_failure_falls_to_claude_then_pi(tmp_path, monkeypatch, claude_ready, failure, kind) -> None:
    codex_failure = {"type": "turn.failed", "error": {"message": failure}}
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
    assert f"codex (headless): {kind}" in usage["fell_back"][0]
    assert calls(tmp_path, "codex")
    await rt.stop()


async def test_recoverable_error_does_not_discard_completed_turn(tmp_path, monkeypatch):
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(
        THREAD, {"type": "error", "message": "429 rate limit exceeded"}, MESSAGE, DONE
    ))
    text, usage = await a.send_turn("x")
    assert text == "done"
    assert usage["input_tokens"] == 80
    assert len(calls(tmp_path, "codex")) == 1


async def test_cumulative_usage_across_turns_and_restored_adapter(tmp_path, monkeypatch):
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(THREAD, MESSAGE, DONE))
    _, first = await a.send_turn("one")
    second_done = {"type": "turn.completed", "usage": {
        "input_tokens": 180, "cached_input_tokens": 60,
        "output_tokens": 20, "cache_write_input_tokens": 10,
    }}
    make_fake_cli(tmp_path, "codex", stdout=jsonl(THREAD, MESSAGE, second_done))
    _, second = await a.send_turn("two")
    restored = CodexAdapter(AdapterConfig(codex_usage=second["codex_usage"]), tmp_path, second["session_id"])
    third_done = {"type": "turn.completed", "usage": {
        "input_tokens": 200, "cached_input_tokens": 70,
        "output_tokens": 25, "cache_write_input_tokens": 12,
    }}
    make_fake_cli(tmp_path, "codex", stdout=jsonl(THREAD, MESSAGE, third_done))
    _, third = await restored.send_turn("three")
    keys = ("input_tokens", "cache_read_input_tokens", "output_tokens", "cache_creation_input_tokens")
    assert [second[k] for k in keys] == [40, 20, 8, 2]
    assert [third[k] for k in keys] == [10, 10, 5, 2]
    assert [sum(u[k] for u in (first, second, third)) for k in keys] == [130, 70, 25, 12]
    restored.adopt_session("new-thread")
    make_fake_cli(tmp_path, "codex", stdout=jsonl({"type": "thread.started", "thread_id": "new-thread"}, MESSAGE, DONE))
    _, fresh = await restored.send_turn("fresh")
    assert fresh["input_tokens"] == 80


async def test_cached_input_is_not_counted_as_fresh(tmp_path, monkeypatch):
    done = {"type": "turn.completed", "usage": {"input_tokens": 24763, "cached_input_tokens": 24448}}
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(THREAD, MESSAGE, done))
    _, usage = await a.send_turn("x")
    assert usage["input_tokens"] == 315
    assert usage["cache_read_input_tokens"] == 24448


async def test_cached_only_usage_is_recorded():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from hive.models.entity import Entity
    from hive.process.manager import ProcessManager

    store = SimpleNamespace(record=AsyncMock())
    manager = SimpleNamespace(token_store=store)
    entity = Entity(name="n", role="lead")
    usage = {"input_tokens": 0, "cache_read_input_tokens": 100, "model": "gpt-6.1-sol"}
    await ProcessManager._record_usage(manager, entity, usage)
    store.record.assert_awaited_once_with("n", {"model": entity.model, **usage})
