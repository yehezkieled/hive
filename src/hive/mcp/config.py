"""Generate per-entity MCP server config files for --mcp-config."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def generate_mcp_config(entity_name: str, config_path: str | Path) -> None:
    """Write a Claude Code --mcp-config JSON for one entity.

    Two stdio MCP servers are launched per entity:
    - ``hive``: advisor (one-shot Opus review). Sprint 13.
    - ``hive-knowledge``: ``search_knowledge`` over blueprints + attachments. Sprint 27.

    The knowledge server is gated by ``HIVE_KNOWLEDGE_MCP_ENABLED`` so it can
    be killed independently of the advisor. Uses ``sys.executable`` so the
    same venv Python runs both servers.
    """
    knowledge_enabled = os.environ.get("HIVE_KNOWLEDGE_MCP_ENABLED", "true").lower() == "true"

    servers: dict[str, dict] = {
        "hive": {
            "command": sys.executable,
            "args": [
                "-m",
                "hive.mcp.advisor_server",
                "--entity",
                entity_name,
            ],
        }
    }
    if knowledge_enabled:
        servers["hive-knowledge"] = {
            "command": sys.executable,
            "args": [
                "-m",
                "hive.mcp.knowledge_server",
                "--entity",
                entity_name,
            ],
        }

    config = {"mcpServers": servers}
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)
