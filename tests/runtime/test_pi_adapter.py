"""PiAdapter against a fake ``pi`` executable (real subprocess path)."""

from __future__ import annotations

import pytest

import hive.config as config
from hive.runtime.adapter_config import AdapterConfig
from hive.runtime.harness import HarnessError
from hive.runtime.harness import HarnessErrorKind as K
from hive.runtime.pi_adapter import PiAdapter, probe_pi
from tests.runtime.fake_cli import calls, jsonl, make_fake_cli

_HEADER = {"type": "session", "version": 3, "id": "pi-sess-1", "cwd": "/x"}


def _assistant(text: str, **extra) -> dict:
    return {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": text}],
            "stopReason": "stop",
            "usage": {"input": 20, "output": 5, "cacheRead": 80, "cacheWrite": 2,
                      "cost": {"total": 0.0}},
            **extra,
        },
    }  # fmt: skip


def _adapter(tmp_path, monkeypatch, **fake) -> PiAdapter:
    monkeypatch.setattr(config, "PI_BINARY", str(make_fake_cli(tmp_path, "pi", **fake)))
    monkeypatch.setattr(config, "PI_MODEL", "")
    monkeypatch.setattr(config, "PI_PROVIDER", "")
    return PiAdapter(AdapterConfig(name="otter", role="maestro"), cwd=tmp_path)


async def test_success_parses_last_assistant_message(tmp_path, monkeypatch) -> None:
    out = jsonl(
        _HEADER,
        {"type": "agent_start"},
        {"type": "message_end", "message": {"role": "user", "content": "hi"}},
        _assistant("first"),
        _assistant("final answer"),
        {"type": "agent_settled"},
    )
    a = _adapter(tmp_path, monkeypatch, stdout=out)

    text, usage = await a.send_turn("do it")

    assert text == "final answer"
    assert usage["input_tokens"] == 100  # last call: input 20 + cacheRead 80
    assert usage["output_tokens"] == 10  # summed across calls
    assert usage["session_id"] == "pi-sess-1"
    assert usage["cost_usd"] is None
    call = calls(tmp_path, "pi")[0]
    assert call["stdin"] == "do it"
    assert call["argv"][:4] == ["-p", "--mode", "json", "--session-id"]
    assert "--append-system-prompt" in call["argv"]
    assert "--model" not in call["argv"]  # Claude aliases mean nothing to Pi


async def test_model_and_provider_are_passed_only_when_configured(tmp_path, monkeypatch) -> None:
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(_HEADER, _assistant("ok")))
    monkeypatch.setattr(config, "PI_MODEL", "sonnet:high")
    monkeypatch.setattr(config, "PI_PROVIDER", "anthropic")
    await a.send_turn("x")
    argv = calls(tmp_path, "pi")[0]["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet:high"
    assert argv[argv.index("--provider") + 1] == "anthropic"


async def test_role_model_override(tmp_path, monkeypatch) -> None:
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(_HEADER, _assistant("ok")))
    monkeypatch.setenv("HIVE_PI_MODEL_MAESTRO", "custom-pi")
    await a.send_turn("x")
    argv = calls(tmp_path, "pi")[0]["argv"]
    assert argv[argv.index("--model") + 1] == "custom-pi"


async def test_session_id_is_stable_across_turns_and_restarts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "PI_BINARY",
        str(make_fake_cli(tmp_path, "pi", stdout=jsonl(_HEADER, _assistant("ok")))),
    )
    a = PiAdapter(AdapterConfig(name="n"), cwd=tmp_path, resume_session_id="persisted-1")
    await a.send_turn("one")
    await a.send_turn("two")
    ids = [c["argv"][c["argv"].index("--session-id") + 1] for c in calls(tmp_path, "pi")]
    assert ids == ["persisted-1", "pi-sess-1"]  # follows the id Pi reports in its header


async def test_no_credentials_is_auth(tmp_path, monkeypatch) -> None:
    # Captured from the installed pi with no provider configured: header on
    # stdout, message on stderr, exit 1.
    a = _adapter(
        tmp_path,
        monkeypatch,
        stdout=jsonl(_HEADER),
        stderr="No API key found for the selected model.\n\nUse /login to log into a provider",
        rc=1,
    )
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.AUTH and ei.value.falls_back
    assert ei.value.harness == "pi"


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        ("429 rate limit exceeded", K.QUOTA),
        ("401 invalid x-api-key", K.AUTH),
        ("provider closed the connection", K.OTHER),
    ],
)
async def test_model_error_inside_the_run(tmp_path, monkeypatch, error, kind) -> None:
    failed = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [],
            "stopReason": "error",
            "errorMessage": error,
        },
    }
    a = _adapter(tmp_path, monkeypatch, stdout=jsonl(_HEADER, failed), rc=0)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is kind


async def test_quota_after_a_tool_call_is_not_replayed_elsewhere(tmp_path, monkeypatch) -> None:
    acted = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "toolCall", "id": "t1", "name": "bash", "arguments": {}}],
            "stopReason": "toolUse",
        },
    }
    failed = {
        "type": "message_end",
        "message": {"role": "assistant", "content": [], "stopReason": "error",
                    "errorMessage": "429 rate limit exceeded"},
    }  # fmt: skip
    out = jsonl(_HEADER, acted, {"type": "tool_execution_start", "toolCallId": "t1"}, failed)
    a = _adapter(tmp_path, monkeypatch, stdout=out, rc=0)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.OTHER and not ei.value.falls_back


async def test_retries_exhausted(tmp_path, monkeypatch) -> None:
    out = jsonl(
        _HEADER, {"type": "auto_retry_end", "success": False, "finalError": "429 quota exceeded"}
    )
    a = _adapter(tmp_path, monkeypatch, stdout=out, rc=1)
    with pytest.raises(HarnessError) as ei:
        await a.send_turn("x")
    assert ei.value.kind is K.QUOTA


# --- probe ------------------------------------------------------------------


def _probe(tmp_path, monkeypatch, provider="", **fake):
    monkeypatch.setattr(config, "PI_BINARY", str(make_fake_cli(tmp_path, "pi", **fake)))
    monkeypatch.setattr(config, "PI_PROVIDER", provider)
    return probe_pi()


def test_probe_signed_in_lists_models(tmp_path, monkeypatch) -> None:
    s = _probe(tmp_path, monkeypatch, stdout="provider  model\nanthropic  claude-sonnet\n")
    assert s.installed and s.signed_in is True


def test_probe_no_models_means_signed_out(tmp_path, monkeypatch) -> None:
    # Captured from the installed pi (0.87) with no provider configured.
    s = _probe(
        tmp_path, monkeypatch, stderr="No models available. Use /login to log into a provider"
    )
    assert s.installed and s.signed_in is False and not s.usable


def test_probe_with_provider_uses_auth_check(tmp_path, monkeypatch) -> None:
    ready = _probe(tmp_path, monkeypatch, provider="anthropic", stdout='{"status":"ready"}')
    assert ready.signed_in is True
    assert calls(tmp_path, "pi")[0]["argv"][:2] == ["auth", "check"]
    not_ready = _probe(
        tmp_path, monkeypatch, provider="anthropic",
        stdout='{"status":"not_ready","reason":"credentials_not_configured"}', rc=1,
    )  # fmt: skip
    assert not_ready.signed_in is False


def test_probe_not_installed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "PI_BINARY", str(tmp_path / "missing"))
    s = probe_pi()
    assert not s.installed and not s.usable
