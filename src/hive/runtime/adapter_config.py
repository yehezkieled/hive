"""Harness-neutral adapter settings and the prompt blocks every harness shares.

One ``AdapterConfig`` describes an entity to *any* adapter (Claude PTY, Claude
headless, Pi, ...). Fields a harness cannot honour are ignored by that adapter —
e.g. Pi has no MCP/ownership-guard hooks. The identity + role-JD prompt blocks
are plain text, so they live here instead of in a Claude adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AdapterConfig:
    """Everything an adapter needs to launch one entity on its harness."""

    model: str = ""
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "default"
    role: str = "lead"
    name: str = ""
    mcp_config_path: Path | None = None
    advisor: str | None = None
    # Per-spawn settings file carrying the ownership-guard PreToolUse hook
    # (Ticket 024, ADR 0017). Injected via --settings; None = no fence.
    # Claude-only: other harnesses have no equivalent hook.
    settings_path: Path | None = None
    codex_usage: dict = field(default_factory=dict)


def build_system_prompts(cfg: AdapterConfig) -> list[str]:
    """The append-system-prompt blocks, in order, for any harness."""
    prompts: list[str] = []
    if cfg.system_prompt:
        prompts.append(cfg.system_prompt)
    identity_lines = [
        f"You are {cfg.name}. Your role is {cfg.role}.",
        "If a hive_action is denied or fails, report the failure honestly. "
        "Do not narrate fictional success.",
    ]
    prompts.append("\n".join(identity_lines))
    return prompts
