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
        pool.execute = AsyncMock(return_value=None)

        store = AdvisorStore(pool)
        result = await store.record("dev", "context", "response", 100, 50, 0.01, "success")
        pool.execute.assert_called()
        assert result is None

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


# ── advisor() tool function tests ────────────────────────────────────────────
class TestAdvisorTool:
    """Tests for the advisor() async tool in advisor_server.py.

    All tests mock the DB pool and subprocess to avoid real connections.
    """

    def setup_method(self) -> None:
        """Set up module-level globals before each test."""
        import hive.mcp.advisor_server as advisor_server

        # Pre-set globals so _get_pool() and entity_name check don't block
        self._advisor_server = advisor_server
        self._orig_entity_name = advisor_server._entity_name
        self._orig_pool = advisor_server._pool
        advisor_server._entity_name = "test"

    def teardown_method(self) -> None:
        """Restore module-level globals after each test."""
        self._advisor_server._entity_name = self._orig_entity_name
        self._advisor_server._pool = self._orig_pool

    async def test_rate_limit_branch(self) -> None:
        """advisor() returns a rate-limit message when called too soon after last call."""
        import hive.mcp.advisor_server as advisor_server
        from hive.bus.advisor_store import AdvisorStore

        # Build a mock pool
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock(return_value=None)
        advisor_server._pool = mock_pool

        # get_last_call returns very recent timestamp → triggers rate limit
        with patch.object(AdvisorStore, "get_last_call", new=AsyncMock(return_value=datetime.now(UTC))):
            with patch.object(AdvisorStore, "record", new=AsyncMock(return_value=None)):
                result = await advisor_server.advisor(context="test")

        assert any(
            word in result.lower() for word in ("rate", "cooldown", "limit")
        ), f"Expected rate-limit message, got: {result!r}"

    async def test_daily_limit_branch(self) -> None:
        """advisor() returns a daily-limit message when count_today reaches ADVISOR_DAILY_LIMIT."""
        import hive.mcp.advisor_server as advisor_server
        from hive.bus.advisor_store import AdvisorStore
        from hive.config import ADVISOR_DAILY_LIMIT

        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock(return_value=None)
        advisor_server._pool = mock_pool

        with patch.object(AdvisorStore, "get_last_call", new=AsyncMock(return_value=None)):
            with patch.object(AdvisorStore, "count_today", new=AsyncMock(return_value=ADVISOR_DAILY_LIMIT)):
                with patch.object(AdvisorStore, "record", new=AsyncMock(return_value=None)):
                    result = await advisor_server.advisor(context="test")

        assert any(
            word in result.lower() for word in ("limit", "daily")
        ), f"Expected daily-limit message, got: {result!r}"

    async def test_happy_path(self) -> None:
        """advisor() returns the Opus response text on a successful subprocess call."""
        import hive.mcp.advisor_server as advisor_server
        from hive.bus.advisor_store import AdvisorStore

        # Canned stream-json output from a one-shot claude -p call
        canned_output = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Looks good."}]
                },
            }
        ).encode()

        # Mock asyncpg pool with fetch returning empty message list
        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.execute = AsyncMock(return_value=None)
        advisor_server._pool = mock_pool

        # Mock the subprocess
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(canned_output, b""))

        with patch.object(AdvisorStore, "get_last_call", new=AsyncMock(return_value=None)):
            with patch.object(AdvisorStore, "count_today", new=AsyncMock(return_value=0)):
                with patch.object(AdvisorStore, "record", new=AsyncMock(return_value=None)):
                    with patch("hive.mcp.advisor_server.asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
                        result = await advisor_server.advisor(context="")

        assert "Looks good." in result, f"Expected Opus response, got: {result!r}"
