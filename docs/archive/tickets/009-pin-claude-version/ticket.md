# 009 — Pin & align the fleet's Claude Code version

## What

The host has two independent Claude Code installs: the native installer
(`~/.local/bin/claude` → `~/.local/share/claude/versions/`,
self-updating, currently **2.1.162**) and the npm global
(`/usr/lib/node_modules/@anthropic-ai/claude-code` → `/usr/bin/claude`,
currently **2.1.140**). The `hive.service` systemd PATH omits
`~/.local/bin`, so the fleet silently spawns the npm one (2.1.140) while
dev runs the native one (2.1.162). Pick one install + a known version
for the fleet, make resolution explicit (service PATH or an absolute
binary), and log the resolved `claude --version` at spawn.

## Why

Hive's PTY adapter scrapes the Claude Code TUI and is, by
[ADR 0001](../../adr/0001-harness-agnostic-runtime.md)'s own note,
"sensitive to Claude Code TUI changes." Running a 22-version-old CC in
the fleet while developing/testing against a newer one risks silent
gate-detection / output-parsing breakage, and the gap only widens (the
npm install is frozen until a manual `npm i -g`). `claude doctor`
inspects the native install, not the npm one the service runs, so the
drift is invisible to it.

## Acceptance

- The fleet resolves a single, known Claude Code version
  deterministically (not via an ambiguous PATH lookup).
- The resolved `claude` version is logged at PtySession spawn, so drift
  is visible in the journal.
- Dev and fleet either share one install or have a documented,
  intentional version policy — no silent independent drift.
- The deploy runbook (`docs/DEPLOYMENT.md`) records the version policy.

## Cross-cutting

✱ Edits the systemd unit and `docs/DEPLOYMENT.md` — declare the
reference-doc impact in `plan.md`.
