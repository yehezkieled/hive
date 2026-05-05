"""Generate per-entity MCP server config files for --mcp-config."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def generate_mcp_config(entity_name: str, config_path: str | Path) -> None:
    """Write a Claude Code --mcp-config JSON for one entity.

    The config launches hive.mcp.advisor_server as a stdio MCP server.
    Uses sys.executable so the same venv Python runs the server.
    """
    config = {
        "mcpServers": {
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
    }
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)
