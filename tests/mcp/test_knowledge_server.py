"""Unit tests for the search_knowledge MCP tool (Sprint 27)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestSearchKnowledgeValidation:
    """Argument validation — invalid kind/limit/empty query return clean strings."""

    async def test_invalid_kind_returns_error_string(self) -> None:
        import hive.mcp.knowledge_server as ks

        result = await ks.search_knowledge("anything", kind="bogus")
        assert "Invalid kind" in result

    async def test_empty_query_returns_error_string(self) -> None:
        import hive.mcp.knowledge_server as ks

        result = await ks.search_knowledge("   ", kind="both")
        assert "empty" in result.lower()

    async def test_zero_limit_returns_error_string(self) -> None:
        import hive.mcp.knowledge_server as ks

        result = await ks.search_knowledge("anything", kind="both", limit=0)
        assert "Invalid limit" in result


class TestSearchKnowledgeRouting:
    """``kind`` routes to the right store(s) and formats results."""

    async def test_blueprints_only(self) -> None:
        import hive.mcp.knowledge_server as ks

        ks._pool = MagicMock()  # bypass _get_pool

        bp_hits = [
            {
                "id": 1,
                "title": "Auth design",
                "chunk_text": "bearer tokens expire after 1h",
                "chunk_index": 0,
                "distance": 0.12,
            }
        ]

        with (
            patch.object(ks, "_get_pool", new=AsyncMock(return_value=ks._pool)),
            patch(
                "hive.knowledge.blueprints.BlueprintStore.search",
                new=AsyncMock(return_value=bp_hits),
            ),
            patch(
                "hive.bus.attachment_store.AttachmentStore.search",
                new=AsyncMock(return_value=[]),
            ) as att_search,
        ):
            result = await ks.search_knowledge("auth", kind="blueprints")
            att_search.assert_not_called()

        assert "## Blueprints" in result
        assert "Auth design" in result
        assert "bearer tokens" in result
        assert "## Attachments" not in result

    async def test_attachments_only(self) -> None:
        import hive.mcp.knowledge_server as ks

        ks._pool = MagicMock()
        att_hits = [
            {
                "file_path": "/tmp/uploads/brief.md",
                "original_name": "brief.md",
                "mime_type": "text/markdown",
                "chunk_text": "rate-limit playbook for the gateway",
                "distance": 0.21,
            }
        ]

        with (
            patch.object(ks, "_get_pool", new=AsyncMock(return_value=ks._pool)),
            patch(
                "hive.knowledge.blueprints.BlueprintStore.search",
                new=AsyncMock(return_value=[]),
            ) as bp_search,
            patch(
                "hive.bus.attachment_store.AttachmentStore.search",
                new=AsyncMock(return_value=att_hits),
            ),
        ):
            result = await ks.search_knowledge("rate limit", kind="attachments")
            bp_search.assert_not_called()

        assert "## Attachments" in result
        assert "/tmp/uploads/brief.md" in result
        assert "rate-limit playbook" in result
        assert "## Blueprints" not in result

    async def test_both_kinds(self) -> None:
        import hive.mcp.knowledge_server as ks

        ks._pool = MagicMock()

        with (
            patch.object(ks, "_get_pool", new=AsyncMock(return_value=ks._pool)),
            patch(
                "hive.knowledge.blueprints.BlueprintStore.search",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": 1,
                            "title": "Auth",
                            "chunk_text": "tokens",
                            "chunk_index": 0,
                            "distance": 0.1,
                        }
                    ]
                ),
            ),
            patch(
                "hive.bus.attachment_store.AttachmentStore.search",
                new=AsyncMock(
                    return_value=[
                        {
                            "file_path": "/tmp/x.md",
                            "original_name": "x.md",
                            "mime_type": "text/markdown",
                            "chunk_text": "policy",
                            "distance": 0.2,
                        }
                    ]
                ),
            ),
        ):
            result = await ks.search_knowledge("anything", kind="both")

        assert "## Blueprints" in result
        assert "## Attachments" in result

    async def test_empty_results_returns_friendly_message(self) -> None:
        import hive.mcp.knowledge_server as ks

        ks._pool = MagicMock()

        with (
            patch.object(ks, "_get_pool", new=AsyncMock(return_value=ks._pool)),
            patch(
                "hive.knowledge.blueprints.BlueprintStore.search",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "hive.bus.attachment_store.AttachmentStore.search",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = await ks.search_knowledge("nothing matches", kind="both")

        assert result == "No matching knowledge found."

    async def test_blueprint_search_failure_isolated(self) -> None:
        """A failing blueprint search must not break attachment search."""
        import hive.mcp.knowledge_server as ks

        ks._pool = MagicMock()

        async def boom(*args, **kwargs):
            raise RuntimeError("db down")

        with (
            patch.object(ks, "_get_pool", new=AsyncMock(return_value=ks._pool)),
            patch(
                "hive.knowledge.blueprints.BlueprintStore.search",
                new=boom,
            ),
            patch(
                "hive.bus.attachment_store.AttachmentStore.search",
                new=AsyncMock(
                    return_value=[
                        {
                            "file_path": "/tmp/x.md",
                            "original_name": "x.md",
                            "mime_type": "text/markdown",
                            "chunk_text": "policy",
                            "distance": 0.2,
                        }
                    ]
                ),
            ),
        ):
            result = await ks.search_knowledge("anything", kind="both")

        assert "search failed" in result
        assert "## Attachments" in result
        assert "/tmp/x.md" in result


# ── generate_mcp_config Sprint 27 changes ───────────────────────────────────
class TestKnowledgeServerRegistration:
    def test_knowledge_server_present_by_default(self, tmp_path: Path) -> None:
        from hive.mcp.config import generate_mcp_config

        config_path = tmp_path / "mcp.json"
        generate_mcp_config("dev", str(config_path))
        data = json.loads(config_path.read_text())

        assert "hive" in data["mcpServers"]
        assert "hive-knowledge" in data["mcpServers"]

        knowledge = data["mcpServers"]["hive-knowledge"]
        assert knowledge["command"] == sys.executable
        assert "hive.mcp.knowledge_server" in knowledge["args"]
        assert "--entity" in knowledge["args"]
        idx = knowledge["args"].index("--entity")
        assert knowledge["args"][idx + 1] == "dev"

    def test_knowledge_server_disabled_via_env(self, tmp_path: Path, monkeypatch) -> None:
        from hive.mcp.config import generate_mcp_config

        monkeypatch.setenv("HIVE_KNOWLEDGE_MCP_ENABLED", "false")
        config_path = tmp_path / "mcp.json"
        generate_mcp_config("dev", str(config_path))
        data = json.loads(config_path.read_text())

        assert "hive" in data["mcpServers"]
        assert "hive-knowledge" not in data["mcpServers"]
