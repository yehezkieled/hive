"""Generate per-entity MCP server config files for --mcp-config."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _knowledge_enabled() -> bool:
    return os.environ.get("HIVE_KNOWLEDGE_MCP_ENABLED", "true").lower() == "true"


def mcp_servers_enabled() -> bool:
    """True iff at least one MCP server would be configured for an entity.

    The custom advisor server was retired (Ticket 013, ADR 0009); the
    knowledge server is the only remaining one. The fleet passes
    ``--mcp-config`` iff this is True, so no empty config is ever written or
    handed to Claude Code.
    """
    return _knowledge_enabled()


def generate_mcp_config(entity_name: str, config_path: str | Path) -> None:
    """Write a Claude Code --mcp-config JSON for one entity.

    One stdio MCP server may be launched per entity:
    - ``hive-knowledge``: ``search_knowledge`` over blueprints + attachments
      (Sprint 27), gated by ``HIVE_KNOWLEDGE_MCP_ENABLED``.

    Uses ``sys.executable`` so the same venv Python runs the server. Writes
    nothing when no server is enabled — callers gate on ``mcp_servers_enabled``.
    """
    servers: dict[str, dict] = {}
    if _knowledge_enabled():
        servers["hive-knowledge"] = {
            "command": sys.executable,
            "args": [
                "-m",
                "hive.mcp.knowledge_server",
                "--entity",
                entity_name,
            ],
        }

    if not servers:
        return

    config = {"mcpServers": servers}
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)
