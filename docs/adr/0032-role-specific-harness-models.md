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
- Use `claude-opus-5-5` for Maestros and `claude-sonnet-5-5` for other roles
  when Claude serves a turn. The model applies to both headless and PTY modes.
- `HIVE_CLAUDE_MODEL_<ROLE>`, `HIVE_CODEX_MODEL_<ROLE>`,
  `HIVE_CODEX_EFFORT_<ROLE>`, and `HIVE_PI_MODEL_<ROLE>` override the defaults
  per role. Codex and Pi retain their global model settings as fallbacks.
- Record the serving harness's selected Claude or Codex model with turn usage.

## Consequences

An Entity's stored model field can differ from the model that serves a turn;
token accounting records the serving model. Role-level environment settings
are authoritative for these harnesses.
