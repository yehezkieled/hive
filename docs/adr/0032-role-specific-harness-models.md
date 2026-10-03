# Role-specific harness model defaults

## Status

Accepted, 2026-10-03. Refines [ADR 0031](0031-codex-headless-harness.md).

## Context

The captain wants the Maestro to orchestrate with Claude Opus 5.5. Other roles
should use Codex GPT-6.1 Sol at medium effort, with Claude Sonnet 5.5 and Pi as
fallbacks. The Vault still needs its Claude-only lockdown.

## Decision

- Keep the role order from ADR 0031: Maestro and Vault start with Claude;
  other roles start with Codex. Pi remains last.
- Default to `claude-opus-5-5` for Maestros and `claude-sonnet-5-5` for other
  roles when Claude serves a turn. In both headless and PTY modes, an explicit
  entity/personality model choice wins; otherwise use the role environment
  setting, then the role default.
- `HIVE_CLAUDE_MODEL_<ROLE>`, `HIVE_CODEX_MODEL_<ROLE>`,
  `HIVE_CODEX_EFFORT_<ROLE>`, and `HIVE_PI_MODEL_<ROLE>` override the defaults
  per role. Codex and Pi retain their global model settings as fallbacks.
- Record the serving harness's selected Claude or Codex model with turn usage.

## Consequences

An Entity's stored model field can differ from the model that serves a turn;
token accounting records the serving model. Codex and Pi select their models
from harness configuration rather than the Entity's Claude model choice; see
[the deployment runbook](../DEPLOYMENT.md#harness-selection-adr-0029) for
configuration and compatibility guidance.
