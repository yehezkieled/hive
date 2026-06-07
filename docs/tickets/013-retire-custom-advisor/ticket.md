# 013 — Retire Hive's custom advisor; adopt Claude Code's native `/advisor`

## What

Hive ships its own "advisor" as an MCP tool
(`src/hive/mcp/advisor_server.py`): an Entity calls `advisor(context)`, Hive
pulls that Entity's recent messages from the Postgres bus, builds an Opus
review prompt, and **spawns a one-shot `claude -p --model opus`** subprocess,
returning the response. It is rate-limited (5 min / 20 per day) and logged via
the `advisor_calls` table.

Claude Code now provides this natively: the `/advisor` tool (since CC
**2.1.101**) pairs Opus as a senior adviser with the Entity's executor model.
When the executor is unsure, it calls `advisor()`; Opus reasons over the
session and returns guidance — **in-process, no separate `claude` spawn, no
MCP server**.

Retire the custom advisor and adopt the native one: delete
`advisor_server.py` + `advisor_store.py` + the MCP registration + the
`ADVISOR_*` config + its tests; add a migration to drop `advisor_calls`;
enable CC's native `/advisor` for Entities and point the role JDs at it.

## Why

The custom advisor now **duplicates** a native Claude Code feature — and does
so via a separate `claude -p` subprocess, which is a second
binary-resolution / version-drift surface (exactly what Ticket
[009](../009-pin-claude-version/) hardens) *and* a separate billing path. The
native advisor runs **inside** the Entity's CC session, so it inherits the
pinned version for free and is **Plan-billed** rather than a separate metered
call. Removing ~14 files of bespoke code in favour of a maintained native
equivalent is a net simplification.

## Acceptance

- Hive no longer spawns `claude -p` anywhere; `advisor_server.py`,
  `advisor_store.py`, and `test_advisor_mcp.py` are deleted, and every
  reference (`config.py`, `mcp/config.py`, `models/entity.py`,
  `runtime/pty_session.py`, `process/{manager,lifecycle_manager,message_dispatcher}.py`,
  and their tests) is removed or repointed.
- A new migration drops the `advisor_calls` table (migration `015` stays —
  applied migration history is append-only).
- CC's native `/advisor` is available to Entities, and the role JDs prompt its
  use where the custom advisor was referenced.
- `ruff` + full `pytest -m "not integration"` green; a maestro turn completes
  end-to-end.

## Open design questions (resolve in research/design)

- **Context parity** — the custom advisor feeds Opus the Entity's recent
  *bus* history (cross-turn, possibly cross-Entity). Native `/advisor`
  forwards the *current CC session* context. Confirm the native view is
  sufficient, or document what is lost.
- **Rate limiting** — drop the 5 min / 20-day cap, or replace it? The native
  advisor is designed to be invoked only when the executor is unsure.
- **Telemetry** — losing the `advisor_calls` analytics. Acceptable, or replace
  with something lighter?
- **Enablement** — how is the native advisor turned on for Entities given the
  fleet's spawn flags (`--dangerously-skip-permissions`, `--mcp-config`)?
- **Billing** — confirm the native path is Plan-billed in Hive's deployment.

## Non-goals

- Ticket 009's version pinning (separate). 013 assumes the fleet runs a
  CC ≥ 2.1.101 build, which 009 guarantees.
- Any change to non-advisor MCP tools.

## Cross-cutting

✱ Likely edits `docs/DEPLOYMENT.md` (the advisor is described there) and the
role JDs, and adds a DB migration — declare the reference-doc impact in
`plan.md`.

## Sprint

Earmarked for the **next sprint** (provisionally `2026-Q2-S5`) — it is a
behaviour change / feature, out of scope for S4's "zero behaviour change"
hardening theme. The next sprint is opened via `plan-next-sprint` at S4 close
(~2026-06-18); this ticket is a committed candidate for it.
