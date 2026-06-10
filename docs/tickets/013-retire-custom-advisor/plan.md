# Plan — Ticket 013: Retire custom advisor, adopt native `/advisor`

Direct lane: one branch, one PR. Retire the bespoke advisor and enable CC's
native `/advisor` per-Entity with a model-aware default. Build order follows
[`outline.md`](outline.md) (gate decouple **before** delete). Step letters map to
outline groups.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/models/entity.py` | modify | A: parse `**Advisor**:` field; `resolve_advisor()`; append `--advisor` at spawn. B: gate `--mcp-config` on computed signal, drop `ADVISOR_ENABLED` import |
| `src/hive/runtime/pty_session.py` | modify | A: delete `_suppress_advisor`/`_restore_advisor` + call sites (`:175,187,189,241,255,356-385`) |
| `src/hive/mcp/config.py` | modify | B: drop `"hive"` advisor server; `servers` starts empty; write+return path only when ≥1 server |
| `src/hive/process/lifecycle_manager.py` | modify | B: `mcp_config_path` from computed gate, not `ADVISOR_ENABLED` (`:25,127`) |
| `src/hive/process/message_dispatcher.py` | modify | B: gate per-turn `generate_mcp_config` on the computed signal (`:198-199`) |
| `src/hive/process/manager.py` | modify | B: drop `ADVISOR_ENABLED` re-export (`:34`); keep `generate_mcp_config` |
| `src/hive/config.py` | modify | C: remove `ADVISOR_*` (`:202-206`) |
| `src/hive/mcp/advisor_server.py` | **delete** | C |
| `src/hive/bus/advisor_store.py` | **delete** | C |
| `src/hive/bus/migrations/028_drop_advisor_calls.sql` | **create** | D: `DROP TABLE IF EXISTS advisor_calls;` |
| `tests/test_advisor_mcp.py` | **delete** | C/E: migrate `:54-115` coverage into `test_knowledge_server.py` first |
| `tests/mcp/test_knowledge_server.py` | modify | E: assert knowledge-only servers; absorb migrated coverage |
| `tests/process/test_thin_core_smoke.py` | modify | E: drop `ADVISOR_ENABLED` from re-export surface (`:127,135,205`) |
| `tests/process/test_lifecycle_manager.py` | modify | E: fix stale docstring (`:6`) |
| `tests/` (new cases) | create | E: model-aware default + `--advisor` spawn-arg; `--mcp-config` present/absent by knowledge state |
| `personalities/role-worker.md` | modify | F: `**Advisor**: off` |
| `personalities/role-lead.md` | modify | F: default/explicit advisor + one-line nudge |
| `personalities/role-maestro.md` | modify | F: default `off` (or opt-in `opus`) |
| `personalities/_template.md` | modify | F: document the `**Advisor**:` field |
| `docs/DEPLOYMENT.md` | modify | G: 6 advisor spots (`:34,:92,:190,:1105,:1109-1112,:1152`) + `advisorModel` settings note |
| `README.md` | modify | G: source-tree map line (`:154`) |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- Full `pytest -m "not integration"` green
- `grep -rn 'advisor_server\|advisor_store\|ADVISOR_ENABLED\|claude -p' src/ tests/` → clean
  (only the new `--advisor` flag + `**Advisor**:` parsing remain)
- Knowledge search still answers with `HIVE_KNOWLEDGE_MCP_ENABLED=true`; **no**
  `--mcp-config` passed when knowledge is off and advisor removed
- A maestro turn completes end-to-end on deployed code; confirm `advisorModel`
  is set in the deployed `~/.claude/settings.json` (or document setting it) so a
  `--advisor`-on entity actually has the tool

## Out of scope

- Re-adding cross-Entity context to the advisor (session-only is accepted; would
  be its own ticket) — see ADR 0009.
- Ticket 019's Maestro phase-confirmation gate (separate; referenced as the
  Maestro's oversight mechanism).
- Any non-advisor MCP tool change beyond the gate decouple needed to keep
  hive-knowledge alive.
- Ticket 009's version pinning (assumed: fleet runs CC ≥ 2.1.101).

## Cross-cutting impact

This is a ✱ cross-cutting Ticket. Reference-doc edits ride in the same PR:
`docs/DEPLOYMENT.md` (6 spots) and `README.md:154`. Decisions are recorded in
**ADR 0009**; the glossary gains an **Advisor** entry (`CONTEXT.md`). Append-only
migration history is preserved (015/025 untouched; 028 adds the drop).
