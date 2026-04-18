"""Test auto-retrieval of blueprints into agent prompts."""

from __future__ import annotations

from unittest.mock import AsyncMock

from hive.bus.router import MessageRouter
from hive.knowledge.blueprints import BlueprintStore
from hive.models.maestro import Maestro
from hive.process.manager import ProcessManager


async def test_auto_retrieve_prepends_blueprints_to_prompt(
    blueprint_store: BlueprintStore, router: MessageRouter, monkeypatch
):
    """send_to_entity should prepend top-K blueprints as context."""
    # Fake the embedder so we don't hit OpenAI.
    async def fake_embed(texts):
        return [[0.5] + [0.0] * 1535 for _ in texts]

    monkeypatch.setattr("hive.knowledge.blueprints.embed_texts", fake_embed)

    # Seed a blueprint.
    await blueprint_store.save("auth fix", "OAuth redirect pattern", ["auth"])

    mgr = ProcessManager(router=router, blueprint_store=blueprint_store)
    maestro = Maestro(name="dev", model="sonnet", system_prompt="", allowed_tools=[])
    await mgr.register_entity(maestro)

    # Intercept the actual claude -p call.
    captured = {}

    async def fake_send(self, prompt):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.send_prompt", fake_send
    )
    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.start",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.kill",
        AsyncMock(return_value=None),
    )

    await mgr.send_to_entity("dev", "add rate limiting")
    assert "OAuth redirect pattern" in captured["prompt"]
    assert "add rate limiting" in captured["prompt"]


async def test_auto_retrieve_disabled_by_config(
    blueprint_store: BlueprintStore, router: MessageRouter, monkeypatch
):
    async def fake_embed(texts):
        return [[0.5] + [0.0] * 1535 for _ in texts]

    monkeypatch.setattr("hive.knowledge.blueprints.embed_texts", fake_embed)
    monkeypatch.setattr("hive.process.manager.AUTO_RETRIEVE_ENABLED", False)

    await blueprint_store.save("auth fix", "OAuth redirect pattern", ["auth"])

    mgr = ProcessManager(router=router, blueprint_store=blueprint_store)
    maestro = Maestro(name="dev", model="sonnet", system_prompt="", allowed_tools=[])
    await mgr.register_entity(maestro)

    captured = {}

    async def fake_send(self, prompt):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.send_prompt", fake_send
    )
    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.start",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.kill",
        AsyncMock(return_value=None),
    )

    await mgr.send_to_entity("dev", "add rate limiting")
    assert "OAuth redirect" not in captured["prompt"]
