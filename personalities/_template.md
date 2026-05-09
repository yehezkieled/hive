# Entity: <Name>

## Identity
- **Name**: <Name>
- **Role**: maestro | lead | worker
- **Model**: opus | sonnet | haiku

## System Prompt
<System prompt defining this entity's personality, behavior, and purpose.>

## Tools
Pick the tool set that matches the role:
- **Maestro / lead**: `allowedTools: Read Grep Glob` — delegation-only.
  These roles do not write code; they spawn leads or workers.
- **Worker**: `allowedTools: Read Write Edit Bash Grep Glob` — full
  toolkit. Workers do the actual building.
- `disallowedTools`: <space-separated tool names if you need to remove
  specific tools beyond the role default>

## Constraints
<Any constraints or rules this entity must follow.>

## Permission modes
- Default is `edit` — safe, with per-tool prompts for dangerous ops.
- Prefer `yotree` (elevated + sandboxed worktree) for code-heavy work.
- Use `yolo` only for trivial tasks where a worktree is overhead.
- Non-user-owned entities request elevation via
  `request_mode_change` in a hive_actions block with a concrete reason.
