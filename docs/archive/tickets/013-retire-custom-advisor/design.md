# Design — Ticket 013: Retire custom advisor, adopt native `/advisor`

Chosen approach and the alternatives weighed against it. Decisions were settled
in a grill session; this records them and why. See
[`research.md`](research.md) for the evidence and
[ADR 0009](../../adr/0009-adopt-native-advisor.md) for the durable decision
record.

## Decision summary

1. **Retire the custom advisor** entirely — delete the MCP server, the store,
   the dedicated test, the advisor-only config, and **drop** the `advisor_calls`
   table (migration `028`). Rate-limit and telemetry go with it.
2. **Adopt CC native `/advisor`, enabled per-Entity** via a new `**Advisor**:`
   role-file field, passed as `--advisor <model>` at spawn. Default-when-unset is
   **model-aware**. Delete the `_suppress_advisor`/`_restore_advisor` dance.
3. **Decouple the overloaded `ADVISOR_ENABLED` gate** — pass `--mcp-config` based
   on "did `generate_mcp_config` produce any servers?" so hive-knowledge survives.
4. **One PR (direct lane).** Cross-cutting doc edits ride along.

## The per-Entity advisor control

A new field in the role/personality file, mirroring the existing `**Model**:`
field (parsed at `entity.py:91`):

```
**Advisor**: opus      # or sonnet / fable / off
```

- **Translation:** an Entity with `Advisor = <model>` spawns with
  `--advisor <model>`; `off` (or model-aware default = off) spawns with no
  advisor flag. The `--advisor` flag is per-session and authoritative
  (`research.md` §1).
- **Single source of truth:** the global `advisorModel` is removed from fleet
  settings, so an unset flag means the advisor is genuinely off for that session.

### Default-when-unset: model-aware (the cost-driven core decision)

The advisor only pays off when it is **stronger than the main model** — a cheap
main escalating hard calls to a smarter brain. When the main is already Opus, an
Opus advisor is the same brain grading its own work, and each firing costs a
**second full Opus pass**. So:

```
 main model        unset default        rationale
 ─────────         ─────────────        ─────────
 Sonnet / Haiku ─▶ Advisor: opus (ON)   real lift, modest extra cost
 Opus           ─▶ off                  same-tier, double cost, low marginal insight
```

With today's role→model assignments this yields, with **no per-file override**:

| Role | Main | Advisor default | Why |
|------|------|-----------------|-----|
| Team Lead | Sonnet | **Opus (on)** | its independent check; no human gate on a Lead |
| Maestro | Opus | **off** | already top-tier; oversight comes from Ticket 019's human phase-gate, not an Opus-on-Opus pass |
| Worker | Sonnet | **off** (explicit `**Advisor**: off` in `role-worker.md`) | short leaf tasks add little advisor value; Workers are being retired (Phase 3) |

Full freedom retained: any role file or per-spawn value overrides the default —
e.g. set `role-maestro.md` to `**Advisor**: opus` if you want the independent
high-stakes check *in addition to* the 019 human gate.

## The computed `--mcp-config` gate

`generate_mcp_config` (`mcp/config.py`) drops the hard-coded `"hive"` advisor
entry; `servers` starts empty and adds `"hive-knowledge"` only when
`HIVE_KNOWLEDGE_MCP_ENABLED`. The spawn path
(`entity.py`, `lifecycle_manager.py`, `message_dispatcher.py`) stops importing
`ADVISOR_ENABLED` and instead passes `--mcp-config` **iff a config with ≥1 server
was written**. Cleanest shape: `generate_mcp_config` writes the file only when
there is ≥1 server and signals that (path-or-`None`), and callers gate on the
signal. This keeps knowledge search alive and writes no empty config.

## Alternatives considered

| Option | Why rejected |
|--------|--------------|
| **Keep a thin cross-entity context injector** to preserve the old advisor's bus view | The inter-entity exchange is already in both sessions (`research.md` §4); injecting it back re-adds bespoke machinery this ticket exists to delete. Reversible later as its own ticket if ever needed. |
| **Blanket `advisorModel: opus` globally, delete suppress, no per-Entity field** | Simplest, but silently doubles spend on every Opus-main entity (the cost trap the grill surfaced). Rejected for the model-aware per-Entity control. |
| **Default advisor OFF, opt-in per spawn (strict propagation)** | Over-built; the human wanted "on by default, I choose." Opt-in machinery and Maestro→Lead propagation gating was more than asked. |
| **Rename `ADVISOR_ENABLED` → `MCP_CONFIG_ENABLED`** (still a hard flag) | A static flag still needs hand-maintaining as servers come and go, and can pass an empty `--mcp-config`. The computed "any server?" gate is correct by construction. |
| **Keep the 5min/20-day rate cap (custom wrapper)** | Native is model-driven with no cap hook; re-imposing one means wrapping the server tool — defeats the simplification. Plan-quota (QuotaMonitor) is the right cost lever. |
| **Retain `advisor_calls` for analytics** | Native usage already lands in `/usage` + plan quota; the table duplicates QuotaMonitor. |

## Cross-cutting impact (declared up front)

- `docs/DEPLOYMENT.md` — 6 spots (`:34, :92, :190, :1105, :1109-1112, :1152`):
  remove the `claude -p` advisor description, the `HIVE_ADVISOR_*` env rows, the
  migration-015 run note, and the "only remaining metered call" line; note the
  per-Entity `--advisor` enablement + the `advisorModel` settings requirement.
- `README.md:154` — source-tree map line `mcp/ # MCP advisor server`.
- `personalities/role-*.md` — add `**Advisor**:` field; one-line advisor nudge in
  Lead (and Maestro iff opted in).
- `CONTEXT.md` — glossary entry for **Advisor** (native, per-Entity).
- ADR **0009** records the adoption + the model-aware default.

## Deferred to implementation (from `research.md` §8)

Empty-config behaviour with knowledge off; confirming `advisorModel` presence
pre-cutover; role-file parser fallback for an absent `**Advisor**:` field.
