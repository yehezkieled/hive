# 013 — Retire Hive's custom advisor; adopt Claude Code's native `/advisor`

> ## ⚠️ Post-mortem (2026-06-11) — the `--advisor` flag broke every lead
>
> **What happened.** 013 shipped on the premise that Claude Code exposes
> native advisor as a CLI flag (`--advisor <model>`), and `resolve_advisor`
> defaulted a sub-Opus main (any Sonnet lead) to an Opus advisor. But **no CC
> build the fleet runs (2.1.168–172) has a `--advisor` option** — passing it
> makes `claude` exit instantly with `error: unknown option '--advisor'`.
> Maestros (Opus → no advisor) spawned fine; **every lead** (Sonnet → Opus
> advisor) died on spawn, retried 3×, and never ran a turn. This surfaced when
> the first real lead was spawned during the 015 Workflow smoke test — 013's
> own deploy never spawned a lead, so it went unnoticed.
>
> **Root cause.** A flag was emitted that the target binary doesn't support,
> with no capability check — exactly the "the PTY fleet runs a different CC
> than where this was designed" gap. Native `/advisor` (if it exists in
> 2.1.x) is not a `--advisor` CLI flag.
>
> **Fix applied (2026-06-11).**
> - `resolve_advisor` is now **default-off / opt-in** — no role auto-resolves
>   an advisor; only an explicit `**Advisor**:` field turns it on.
> - The adapter **capability-guards** the flag: `_claude_supports_advisor()`
>   probes `claude --help` once and skips `--advisor` (with a warning) when the
>   binary lacks it — so even an explicit opt-in can't crash an unsupported
>   build, and it re-enables for free if a future CC ships the flag.
> - Compensating model change (owner's call): **Opus is now the default model
>   for every spawn** — since the Opus *advisor* (a second-opinion for Sonnet
>   executors) is unavailable, entities run on Opus directly. (Quota cost:
>   Opus draws plan-quota faster; explicit per-spawn `model` still overrides.)
> - The dangling "consult the advisor" instruction was removed from the lead
>   JD (`personalities/role-lead.md`).
>
> **Follow-ups.** (1) Confirm whether 2.1.x exposes native advisor at all and,
> if so, how (it is not a CLI flag) before re-enabling. (2) A capability check
> for *every* spawn flag we pass would have caught this — candidate ticket.
> (3) 013's live behaviour was never smoke-tested (no lead spawned) — pair any
> behaviour-change ticket with a live lead spawn.

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
