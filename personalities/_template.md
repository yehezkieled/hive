# Entity: <Name>

## Identity
- **Name**: <Name>
- **Role**: maestro | lead | worker
- **Model**: opus | sonnet | haiku

## System Prompt
<System prompt defining this entity's personality, behavior, and purpose.>

## Tools
- allowedTools: <comma-separated tool names>
- disallowedTools: <comma-separated tool names>

## Constraints
<Any constraints or rules this entity must follow.>

## Permission modes
- Default is `edit` — safe, with per-tool prompts for dangerous ops.
- Prefer `yotree` (elevated + sandboxed worktree) for code-heavy work.
- Use `yolo` only for trivial tasks where a worktree is overhead.
- Non-user-owned entities request elevation via
  `request_mode_change` in a hive_actions block with a concrete reason.
