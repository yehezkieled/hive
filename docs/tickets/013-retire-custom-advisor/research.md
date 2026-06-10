# Research — Ticket 013: Retire custom advisor

Code-grounded answers to the ticket's open questions, plus two findings the
ticket did not anticipate. Evidence is `file:line`; web-sourced facts are
marked **EXTERNAL**.

## 1. Native `/advisor` is real and adoptable — with one correction

| Claim | Verdict | Evidence |
|-------|---------|----------|
| Exists since CC 2.1.101 | ✅ confirmed | EXTERNAL: `code.claude.com/docs/en/advisor.md`; installed fleet CC is **2.1.170** (`claude --version`) |
| Pairs a stronger advisor with the executor | ✅ | EXTERNAL: advisor must be ≥ main model |
| "In-process, no separate `claude` spawn" | ⚠️ **half-wrong** | EXTERNAL: it's a **server-side tool on Anthropic's infra**, *not* in-process. But the operative facts hold: **no subprocess, no MCP server** |
| Plan-billed on Max | ✅ confirmed | EXTERNAL: "On subscription plans, advisor usage counts toward your plan's usage limits" |
| Sees only the current session | ✅ confirmed | EXTERNAL: "always receives the full conversation" = session transcript |
| Invocation model | **model-driven, at decision points** | EXTERNAL: "Claude decides when to call the advisor… at decision points rather than on every turn"; "no setting to cap or force" |
| Enablement | `/advisor`, `advisorModel` setting, or `--advisor` flag | EXTERNAL: `--advisor` is per-session and takes precedence over the setting |
| Disable entirely | `CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1`, or `/advisor off` | EXTERNAL |
| Pairing rule | advisor ≥ main; Sonnet/Haiku→Opus ✓; Opus→Opus valid ("second Opus reviews first") | EXTERNAL |

**Correction to ticket text:** strike "in-process". It is server-side; the
billing/version-drift wins still stand because there is no subprocess and no MCP
server to maintain.

## 2. Finding A (not in ticket): `_suppress_advisor` rests on a false premise

`pty_session.py:356-385` strips `advisorModel` from `~/.claude/settings.json`
before every spawn, justified by its docstring: *"The Advisor Tool invokes Opus
before every response, adding >90s latency per turn."* The docs flatly
contradict this — native `/advisor` is **model-driven, fires only at decision
points, never every turn** (EXTERNAL). So Hive has been actively *disabling* a
feature based on a wrong belief. Consequence: enabling native `/advisor` does
**not** impose per-turn latency, and the whole suppress/restore dance can be
deleted rather than reworked. The dance also mutates the **shared**
`~/.claude/settings.json` per session ("safe for single-session use" per its own
comment) — a latent concurrency hazard that deletion removes for free.

## 3. Finding B (not in ticket): `ADVISOR_ENABLED` is overloaded

`ADVISOR_ENABLED` does **not** only gate the advisor — it gates the **entire
`--mcp-config` plumbing**, which also carries the unrelated **hive-knowledge**
MCP server (`search_knowledge`, Sprint 27).

- `mcp/config.py:24-44` — `generate_mcp_config` unconditionally builds the
  `"hive"` (advisor) server, then conditionally adds `"hive-knowledge"` (gated by
  `HIVE_KNOWLEDGE_MCP_ENABLED`).
- `models/entity.py:280-283` — `if ADVISOR_ENABLED: args += ['--mcp-config', …]`.
- `process/lifecycle_manager.py:25,127` — `mcp_config_path = … if ADVISOR_ENABLED else None`.
- `process/message_dispatcher.py:198-199` — per-turn `generate_mcp_config()` call gated on `ADVISOR_ENABLED`.
- `process/manager.py:34` — re-exports `ADVISOR_ENABLED` so tests can patch it.

**Naively deleting `ADVISOR_ENABLED` silently kills knowledge search.** The gate
must be **replaced with a computed one**: pass `--mcp-config` iff
`generate_mcp_config` produced ≥1 server. After removing the `"hive"` entry, that
reduces to "is `HIVE_KNOWLEDGE_MCP_ENABLED` on (or any future server)?" and
naturally skips `--mcp-config` when empty — sidestepping the open question of
whether CC tolerates an empty `mcpServers`. `claude_adapter.py:89-93` already
turns a non-None `mcp_config_path` into `--mcp-config <path> --strict-mcp-config`
without naming the advisor, so it needs no advisor-specific edit.

## 4. Context parity is a near-non-loss (answers ticket Q "Context parity")

The custom advisor fed Opus the last 5 bus messages
`WHERE sender=p1 OR recipient=p1` — i.e. the entity's **own** traffic
(`advisor_server.py:101-116`). That exact set is **already in the entity's
session transcript**:

- **Inbound:** peer messages are prepended to the recipient's next prompt —
  `message_dispatcher.py:116`: `"You have pending messages from other entities:\n{inbox}…"`.
- **Outbound:** the entity emitted its own messages as turn output, already in
  its transcript.

So the native advisor (which reads the session) already sees the inter-entity
conversation. The only sliver lost is durable bus history older than the live
session window (post restart/compaction) — an edge case. **Verdict: accept.**

## 5. Removal & repoint map (ticket list verified — no stale claims)

**Delete outright:**
- `src/hive/mcp/advisor_server.py` (FastMCP server, spawns `claude -p` at `:139`)
- `src/hive/bus/advisor_store.py` (`advisor_calls` logging)
- `tests/test_advisor_mcp.py` — but **migrate** its `generate_mcp_config` /
  `mcp_config_path` / gating assertions (`:54-115`) into `tests/mcp/test_knowledge_server.py`, don't just drop the coverage
- `config.py:204-206` advisor-only vars: `ADVISOR_COOLDOWN_SECONDS`, `ADVISOR_DAILY_LIMIT`, `ADVISOR_CONTEXT_MESSAGES`

**Repoint (do NOT delete the wiring):**
- `config.py:203` `ADVISOR_ENABLED` → remove; replace its gate with the computed "any MCP server" check
- `mcp/config.py:15,24-34` — drop the `"hive"` server block + docstring line; keep `"hive-knowledge"`
- `models/entity.py:280-283`, `lifecycle_manager.py:25,127`, `message_dispatcher.py:198-199`, `manager.py:34` — swap `ADVISOR_ENABLED` for the computed gate
- `pty_session.py:175,187,189,241,255,356-385` — delete `_suppress_advisor`/`_restore_advisor` and their call sites

**Tests beyond the ticket's list (found):**
- `tests/process/test_thin_core_smoke.py:127,135,205` — asserts `ADVISOR_ENABLED` in the manager re-export surface; update
- `tests/mcp/test_knowledge_server.py:204-229` — asserts both `"hive"` and `"hive-knowledge"` present; update to knowledge-only
- `tests/process/test_lifecycle_manager.py:6` — stale docstring mention of `test_advisor_mcp`

**Migration:** highest existing is **027** → add `028_drop_advisor_calls.sql`
(`DROP TABLE advisor_calls`). `015_advisor_calls.sql` stays (append-only);
`025_rename_pa_to_otter.sql:35-37` historically UPDATEs the table — leave it,
028 > 025 so ordering is safe. Runner auto-applies in sorted order
(`bus/migrations/runner.py`).

## 6. Enablement mechanism (answers ticket Q "Enablement")

Every Entity spawns with `--dangerously-skip-permissions` and **no allowlist**
(`pty_session.py:107-128`), so the native advisor — a server tool, not
permission-gated — is **not blocked by any tool gate**. The only thing disabling
it today is `_suppress_advisor` stripping `advisorModel`. The 012 denylist and
the lead `Agent`/`Task` guard act on `disallowedTools`, not the advisor
(`skill_curation.py`, `lifecycle_manager.py:62-77`).

**Chosen enablement (per design):** drive it **per-Entity via `--advisor <model>`
at spawn**, not the global `advisorModel` setting. A new `**Advisor**:` field in
the role file (parsed like `**Model**:` at `entity.py:91`) carries the value;
default-when-unset is model-aware (see `design.md`). Removing the global
`advisorModel` from fleet settings makes the per-spawn flag the single source of
truth and keeps "off" entities truly off (no setting + no flag = advisor off,
EXTERNAL).

## 7. Answers to the ticket's remaining open questions

- **Rate limiting** → **drop.** The 5min/20-day cap bounded per-token `claude -p`
  *money*; native is Plan-billed and has no cap mechanism ("no setting to cap or
  force", EXTERNAL). Plan-quota is the new cost vector, already watched by
  `QuotaMonitor`.
- **Telemetry** → **drop** the `advisor_calls` table; native usage shows in
  `/usage` and draws plan quota (QuotaMonitor-tracked). No lightweight
  replacement.
- **Billing** → **Plan-billed** on Hive's Max deployment (EXTERNAL: subscription
  usage counts toward plan limits). The cost watch-out is the **Opus-on-Opus
  double pass** when an Opus-main entity calls an Opus advisor — addressed by the
  model-aware default in `design.md`.

## 8. CONFIRM IN CODE (for the implementer)

- After dropping the `"hive"` entry, confirm `generate_mcp_config` with knowledge
  **off** writes no `--mcp-config` at all (not an empty `{"mcpServers":{}}`).
- Confirm `advisorModel` is actually present in the deployed
  `~/.claude/settings.json` *before* relying on its removal; the move to the
  `--advisor` flag must not assume it.
- Confirm the role-file parser cleanly handles an absent `**Advisor**:` field
  (fall back to the model-aware default) without breaking `**Model**:` parsing.
- `docs/archive/*` and prior-ticket docs (004/007/009) reference the advisor
  historically — **do not edit** (append-only per CLAUDE.md altitude rules).
