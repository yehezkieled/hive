# Codex as Hive's preferred headless harness

## Status

Accepted, 2026-10-03. Extends [ADR 0029](0029-harness-pivot-headless-default-pty-fallback.md).

## Context

Codex CLI is signed in through a ChatGPT subscription. Hive's registry previously
detected Codex but could not run it. The installed model catalog has `gpt-6.1-sol`.

## Decision

- Add a headless Codex adapter using `codex exec --json`, with thread-id resume.
  It uses the installed subscription login, scrubs API-key environment variables,
  and rejects an API-key login at probe time. Codex needs no PTY
  mode because Hive's registry accepts headless-only harnesses.
- Prefer `codex,claude,pi` for unfenced roles. The default Codex model is
  `gpt-6.1-sol` at medium reasoning effort; both are configurable with
  `HIVE_CODEX_MODEL` and `HIVE_CODEX_EFFORT`.
- Keep Claude first for Maestros and the Vault. Their ownership hook and tool
  denylist are Claude-only controls; Codex does not implement equivalent fences.
  Hive flags either role if it falls back to Codex or Pi.
- Classify only Codex's own failed-turn error or process stderr for fallback.
  A failed turn after a command, file change, or MCP tool call is not replayed.
  Codex's `turn.completed` usage provides measured tokens and its thread ID.
  Subscription cost is left unknown. The existing harness-change notification
  reports quota fallback, but no numeric Codex quota or threshold alert is
  available from this CLI stream.

## Consequences

Codex receives Hive's role instructions in the turn prompt because its headless
CLI has no append-system-prompt flag. The `/quota` reading remains specific to
Claude's plan. A Codex quota refusal moves the turn to Claude, then Pi, subject
to fenced-role ordering and the safety check against replaying tool activity.
