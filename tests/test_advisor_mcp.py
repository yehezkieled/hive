"""Unit tests for MCP advisor feature."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ── AdvisorStore tests ───────────────────────────────────────────────────────
class TestAdvisorStore:
    async def test_record_and_get_last_call(self) -> None:
        from hive.bus.advisor_store import AdvisorStore

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": 1})

        store = AdvisorStore(pool)
        row_id = await store.record("dev", "context", "response", 100, 50, 0.01, "success")
        pool.fetchrow.assert_called()
        assert row_id == 1

    async def test_get_last_call_none_when_no_rows(self) -> None:
        from hive.bus.advisor_store import AdvisorStore

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        store = AdvisorStore(pool)
        result = await store.get_last_call("dev")
        assert result is None

    async def test_get_last_call_returns_datetime(self) -> None:
        from hive.bus.advisor_store import AdvisorStore

        pool = MagicMock()
        now = datetime.now(UTC)
        pool.fetchrow = AsyncMock(return_value={"called_at": now})
        store = AdvisorStore(pool)
        result = await store.get_last_call("dev")
        assert result == now

    async def test_count_today_returns_int(self) -> None:
        from hive.bus.advisor_store import AdvisorStore

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=3)
        store = AdvisorStore(pool)
        result = await store.count_today("dev")
        assert result == 3


# ── generate_mcp_config tests ────────────────────────────────────────────────
class TestGenerateMcpConfig:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        from hive.mcp.config import generate_mcp_config

        config_path = tmp_path / "test.json"
        generate_mcp_config("dev.backend", str(config_path))
        data = json.loads(config_path.read_text())
        assert "mcpServers" in data
        assert "hive" in data["mcpServers"]
        server = data["mcpServers"]["hive"]
        assert server["command"] == sys.executable
        assert "--entity" in server["args"]
        assert "dev.backend" in server["args"]

    def test_entity_name_in_args(self, tmp_path: Path) -> None:
        from hive.mcp.config import generate_mcp_config

        config_path = tmp_path / "test2.json"
        generate_mcp_config("pa", str(config_path))
        data = json.loads(config_path.read_text())
        args = data["mcpServers"]["hive"]["args"]
        idx = args.index("--entity")
        assert args[idx + 1] == "pa"

    def test_module_invocation_in_args(self, tmp_path: Path) -> None:
        from hive.mcp.config import generate_mcp_config

        config_path = tmp_path / "test3.json"
        generate_mcp_config("worker", str(config_path))
        data = json.loads(config_path.read_text())
        args = data["mcpServers"]["hive"]["args"]
        assert "-m" in args
        assert "hive.mcp.advisor_server" in args


# ── Entity.mcp_config_path tests ─────────────────────────────────────────────
class TestEntityMcpConfigPath:
    def test_path_contains_entity_name(self) -> None:
        from hive.models.entity import Entity

        e = Entity(name="dev.backend", role="lead")
        assert "dev.backend" in e.mcp_config_path
        assert e.mcp_config_path.endswith(".json")

    def test_build_cli_args_includes_mcp_config_when_enabled(self) -> None:
        from hive.models.entity import Entity

        e = Entity(name="dev", role="maestro")
        with patch("hive.config.ADVISOR_ENABLED", True):
            args = e.build_cli_args()
        assert "--mcp-config" in args
        idx = args.index("--mcp-config")
        assert "dev" in args[idx + 1]

    def test_build_cli_args_excludes_mcp_config_when_disabled(self) -> None:
        from hive.models.entity import Entity

        e = Entity(name="dev", role="maestro")
        with patch("hive.config.ADVISOR_ENABLED", False):
            args = e.build_cli_args()
        assert "--mcp-config" not in args
