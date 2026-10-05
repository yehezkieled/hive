# Entity: <Name>

## Identity
- **Name**: <Name>
- **Role**: vault
- **Model**: opus | sonnet | haiku
- **Advisor**: opus | sonnet | off  *(optional — Claude Code's native
  `/advisor`, a stronger model consulted at decision points. Omit to use the
  default: off for an Opus main; `opus` for a sub-Opus entity. An explicit
  value always wins.)*

## System Prompt
<System prompt defining this entity's personality, behavior, and purpose.>

## Tools
- `allowedTools`: <space-separated tool names>
- `disallowedTools`: <space-separated tool names to remove beyond the role
  default>

## Constraints
<Any constraints or rules this entity must follow.>

## Permission modes
- Default is `edit` — safe, with per-tool prompts for dangerous ops.
- Prefer `yotree` (elevated + sandboxed worktree) for code-heavy work.
- Use `yolo` only for trivial tasks where a worktree is overhead.
- Non-user-owned entities request elevation via
  `request_mode_change` in a hive_actions block with a concrete reason.
