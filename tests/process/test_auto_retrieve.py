"""Test auto-retrieval of blueprints + attachments into agent prompts."""

from __future__ import annotations

from unittest.mock import AsyncMock

from hive.bus.attachment_store import AttachmentStore
from hive.bus.router import MessageRouter
from hive.knowledge.blueprints import BlueprintStore
from hive.models.maestro import Maestro
from hive.process.manager import ProcessManager


async def test_auto_retrieve_prepends_blueprints_to_prompt(
    blueprint_store: BlueprintStore, router: MessageRouter, monkeypatch
):
    """send_to_entity should prepend top-K blueprints as context."""

    # Same vector for query and saved body → cosine distance 0 → passes filter.
    async def fake_embed(texts):
        return [[0.5] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr("hive.knowledge.blueprints.embed_texts", fake_embed)

    await blueprint_store.save("auth fix", "OAuth redirect pattern", ["auth"])

    mgr = ProcessManager(router=router, blueprint_store=blueprint_store)
    maestro = Maestro(name="dev", model="sonnet", system_prompt="", allowed_tools=[])
    await mgr.register_entity(maestro)

    captured = {}

    async def fake_send(self, prompt):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr("hive.process.claude_session.ClaudeSession.send_prompt", fake_send)
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
        return [[0.5] + [0.0] * 1023 for _ in texts]

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

    monkeypatch.setattr("hive.process.claude_session.ClaudeSession.send_prompt", fake_send)
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


async def test_auto_retrieve_max_distance_filters_unrelated_blueprint(
    blueprint_store: BlueprintStore, router: MessageRouter, monkeypatch
):
    """A blueprint orthogonal to the query (distance 1.0) should be dropped."""

    # Each call gets a different one-hot vector → consecutive calls are orthogonal.
    state = {"i": 0}

    async def fake_embed(texts):
        results = []
        for _ in texts:
            vec = [0.0] * 1024
            vec[state["i"] % 1024] = 1.0
            state["i"] += 1
            results.append(vec)
        return results

    monkeypatch.setattr("hive.knowledge.blueprints.embed_texts", fake_embed)
    monkeypatch.setattr("hive.process.manager.AUTO_RETRIEVE_MAX_DISTANCE", 0.5)

    # Save → embed call #0 → blueprint vec[0]=1.
    await blueprint_store.save("auth fix", "OAuth redirect pattern", ["auth"])

    mgr = ProcessManager(router=router, blueprint_store=blueprint_store)
    maestro = Maestro(name="dev", model="sonnet", system_prompt="", allowed_tools=[])
    await mgr.register_entity(maestro)

    captured = {}

    async def fake_send(self, prompt):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr("hive.process.claude_session.ClaudeSession.send_prompt", fake_send)
    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.start",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.kill",
        AsyncMock(return_value=None),
    )

    # send_to_entity → embed call #1 → query vec[1]=1; orthogonal to vec[0].
    await mgr.send_to_entity("dev", "completely different topic")
    assert "OAuth redirect" not in captured["prompt"]
    assert "completely different topic" in captured["prompt"]


# -----------------------------------------------------------------------------
# Sprint 18 — attachment block alongside blueprints
# -----------------------------------------------------------------------------


def _patch_session(monkeypatch, captured: dict) -> None:
    """Stub ClaudeSession.start/send_prompt/kill — captures the prompt."""

    async def fake_send(self, prompt):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr("hive.process.claude_session.ClaudeSession.send_prompt", fake_send)
    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.start",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "hive.process.claude_session.ClaudeSession.kill",
        AsyncMock(return_value=None),
    )


async def _seed_attachment(
    store: AttachmentStore,
    *,
    file_path: str,
    embed_text: str,
    embedding: list[float],
) -> int:
    aid = await store.save(
        file_path=file_path,
        original_name="brief.md",
        mime_type="text/markdown",
        size_bytes=len(embed_text),
        source="web",
        actor=None,
    )
    await store.update_embedding(aid, embedding, embed_text)
    return aid


async def test_auto_retrieve_includes_attachment_block(
    blueprint_store: BlueprintStore,
    attachment_store: AttachmentStore,
    router: MessageRouter,
    monkeypatch,
):
    """Both blueprints and attachments hits should land as separate blocks."""

    # Same vector for every embed call → distance 0 between all pairs.
    async def fake_embed(texts):
        return [[0.5] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr("hive.knowledge.blueprints.embed_texts", fake_embed)
    monkeypatch.setattr("hive.bus.attachment_store.embed_texts", fake_embed)

    await blueprint_store.save("auth fix", "OAuth redirect pattern", ["auth"])
    await _seed_attachment(
        attachment_store,
        file_path="/tmp/uploads/brief.md",
        embed_text="rate-limit playbook for the gateway",
        embedding=[0.5] + [0.0] * 1023,
    )

    mgr = ProcessManager(
        router=router,
        blueprint_store=blueprint_store,
        attachment_store=attachment_store,
    )
    maestro = Maestro(name="dev", model="sonnet", system_prompt="", allowed_tools=[])
    await mgr.register_entity(maestro)

    captured: dict = {}
    _patch_session(monkeypatch, captured)

    await mgr.send_to_entity("dev", "add rate limiting")
    prompt = captured["prompt"]
    assert "Relevant past blueprints" in prompt
    assert "OAuth redirect pattern" in prompt
    assert "Relevant uploaded files" in prompt
    assert "/tmp/uploads/brief.md" in prompt
    assert "rate-limit playbook" in prompt


async def test_auto_retrieve_attachments_disabled_by_config(
    blueprint_store: BlueprintStore,
    attachment_store: AttachmentStore,
    router: MessageRouter,
    monkeypatch,
):
    async def fake_embed(texts):
        return [[0.5] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr("hive.knowledge.blueprints.embed_texts", fake_embed)
    monkeypatch.setattr("hive.bus.attachment_store.embed_texts", fake_embed)
    monkeypatch.setattr("hive.process.manager.AUTO_RETRIEVE_INCLUDE_ATTACHMENTS", False)

    await _seed_attachment(
        attachment_store,
        file_path="/tmp/uploads/brief.md",
        embed_text="rate-limit playbook for the gateway",
        embedding=[0.5] + [0.0] * 1023,
    )

    mgr = ProcessManager(
        router=router,
        blueprint_store=blueprint_store,
        attachment_store=attachment_store,
    )
    maestro = Maestro(name="dev", model="sonnet", system_prompt="", allowed_tools=[])
    await mgr.register_entity(maestro)

    captured: dict = {}
    _patch_session(monkeypatch, captured)

    await mgr.send_to_entity("dev", "add rate limiting")
    assert "Relevant uploaded files" not in captured["prompt"]
    assert "rate-limit playbook" not in captured["prompt"]


async def test_auto_retrieve_attachment_search_failure_isolated(
    blueprint_store: BlueprintStore,
    attachment_store: AttachmentStore,
    router: MessageRouter,
    monkeypatch,
):
    """A failing attachment search must not break blueprint retrieval."""

    async def fake_embed(texts):
        return [[0.5] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr("hive.knowledge.blueprints.embed_texts", fake_embed)

    async def boom(query, limit=5, max_distance=None):
        raise RuntimeError("attachments down")

    monkeypatch.setattr(attachment_store, "search", boom)

    await blueprint_store.save("auth fix", "OAuth redirect pattern", ["auth"])

    mgr = ProcessManager(
        router=router,
        blueprint_store=blueprint_store,
        attachment_store=attachment_store,
    )
    maestro = Maestro(name="dev", model="sonnet", system_prompt="", allowed_tools=[])
    await mgr.register_entity(maestro)

    captured: dict = {}
    _patch_session(monkeypatch, captured)

    await mgr.send_to_entity("dev", "add rate limiting")
    assert "OAuth redirect pattern" in captured["prompt"]
    assert "Relevant uploaded files" not in captured["prompt"]
