"""Real headless runs against the harnesses installed on this host (ADR 0029).

Marked ``integration`` — skipped in CI. Each test skips itself unless its harness
is installed AND signed in, and refuses to run if a per-token API key is in the
environment (these must only ever draw on a subscription/plan login, never spend
API credit). One tiny prompt on the cheapest model per harness.
"""

from __future__ import annotations

import os

import pytest

from hive.runtime.adapter_config import AdapterConfig
from hive.runtime.claude_headless import ClaudeHeadlessAdapter, probe_claude
from hive.runtime.harness import HarnessError
from hive.runtime.harness import HarnessErrorKind as K
from hive.runtime.pi_adapter import PiAdapter, probe_pi

pytestmark = pytest.mark.integration

_API_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY")


def _no_api_billing() -> None:
    present = [k for k in _API_KEYS if os.environ.get(k)]
    if present:
        pytest.skip(f"API key in env ({', '.join(present)}) — refusing to risk paid API credit")


async def test_real_claude_headless_turn(tmp_path) -> None:
    _no_api_billing()
    status = probe_claude()
    if not status.usable:
        pytest.skip(f"claude not usable: {status.describe()}")
    adapter = ClaudeHeadlessAdapter(
        AdapterConfig(model="haiku", name="smoke", role="lead", permission_mode="default"),
        cwd=tmp_path,
    )
    await adapter.start()
    try:
        text, usage = await adapter.send_turn("Reply with exactly the single word: pong")
    except HarnessError as e:
        # A real quota wall is a legitimate outcome — it must classify, not crash.
        assert e.kind in (K.QUOTA, K.REFUSED), e
        pytest.skip(f"claude headless refused/limited: {e}")
    assert "pong" in text.lower()
    assert usage["session_id"] and usage["output_tokens"] > 0

    # Second turn resumes the same conversation (no re-statement of context).
    text2, _ = await adapter.send_turn("What single word did I ask you to reply with?")
    assert "pong" in text2.lower()


async def test_real_pi_headless_turn(tmp_path) -> None:
    _no_api_billing()
    status = probe_pi()
    if not status.usable:
        pytest.skip(f"pi not usable: {status.describe()}")
    adapter = PiAdapter(AdapterConfig(name="smoke", role="lead"), cwd=tmp_path)
    await adapter.start()
    text, usage = await adapter.send_turn("Reply with exactly the single word: pong")
    assert "pong" in text.lower()
    assert usage["session_id"]


async def test_real_pi_signed_out_is_classified_auth(tmp_path) -> None:
    """With no provider configured, the real pi must classify as AUTH (no spend possible)."""
    status = probe_pi()
    if not status.installed or status.signed_in is not False:
        pytest.skip("needs an installed-but-signed-out pi")
    adapter = PiAdapter(AdapterConfig(name="smoke", role="lead"), cwd=tmp_path)
    with pytest.raises(HarnessError) as ei:
        await adapter.send_turn("hi")
    assert ei.value.kind is K.AUTH


async def test_real_claude_logged_out_is_classified_auth(tmp_path, monkeypatch) -> None:
    """Point the real claude at an empty config dir = logged out; no model call is made."""
    status = probe_claude()
    if not status.installed:
        pytest.skip("claude not installed")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty-claude-config"))
    adapter = ClaudeHeadlessAdapter(AdapterConfig(model="haiku", name="smoke"), cwd=tmp_path)
    with pytest.raises(HarnessError) as ei:
        await adapter.send_turn("hi")
    assert ei.value.kind is K.AUTH
    assert "Not logged in" in ei.value.detail
