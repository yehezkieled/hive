# Entity: <Name>

## Identity
- **Name**: <Name>
- **Role**: maestro | lead | worker
- **Model**: opus | sonnet | haiku
- **Advisor**: opus | sonnet | off  *(optional — Claude Code's native
  `/advisor`, a stronger model consulted at decision points. Omit to use the
  default: off for workers and for an Opus main; `opus` for a sub-Opus
  maestro/lead. An explicit value always wins.)*

## System Prompt
<System prompt defining this entity's personality, behavior, and purpose.>

## Tools
Pick the tool set that matches the role:
- **Maestro / lead**: `allowedTools: Read Grep Glob` — delegation-only.
  These roles do not write code; they spawn leads or workers. ALSO add
  `disallowedTools: Agent Task ExitPlanMode TodoWrite TaskCreate TaskUpdate TaskList TaskGet TaskOutput TaskStop`
  — `allowedTools` alone is bypassed under `--dangerously-skip-permissions`,
  but `disallowedTools` is honored, so this is what stops a yolo
  maestro/lead from spawning Claude Code subagents instead of Hive
  workers.
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
