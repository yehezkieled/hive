# 033 — Design

Make a maestro's system prompt state its structural role — **PA Maestro** vs.
**project maestro** — keyed on `is_pa`, so the PA stops mis-identifying. Identity
only: no change to the write-fence (Ticket 024 / ADR 0017) or any policy.

**No ADR** (see Q7). **Direct lane** — one vertical change, one PR.

## Chosen approach

Four moving parts, each at its natural layer:

```
1. SOURCE OF TRUTH   Maestro.is_pa  =  self.name == DEFAULT_MAESTRO       (maestro.py)
        │                 └─ replaces the inline compute at lifecycle_manager.py:311
        ▼
2. CARRY             ClaudeAdapterConfig.is_pa: bool = False              (claude_adapter.py)
        │                 set in _adapter_config_from_entity from entity.is_pa  (lifecycle_manager.py)
        ▼
3. PROMPT TEXT       MAESTRO_IDENTITY = {"pa": "…", "project": "…"}       (loops.py, beside LOOP_PROMPTS)
        │                 appended in _build_pty_system_prompts when role == "maestro", by cfg.is_pa
        ▼
4. NEUTRALIZE        role-maestro.md: opening framing → ownership-neutral; fix stale "Workers" line
```

### 1. `Maestro.is_pa` property (single source of truth)

```python
# maestro.py
from hive.config import DEFAULT_MAESTRO

@property
def is_pa(self) -> bool:
    """True iff this maestro is the PA (the default route, owns no project)."""
    return self.name == DEFAULT_MAESTRO
```

Then `lifecycle_manager.py:311` becomes `is_pa = entity.is_pa` (for a `Maestro`),
so there is exactly one definition. This is the source of truth the ticket's
*Why* says is missing — adding it is consolidation, not new scatter.

### 2. Carry `is_pa` to the prompt builder

Add `is_pa: bool = False` to `ClaudeAdapterConfig`. In `_adapter_config_from_entity`,
set `is_pa=getattr(entity, "is_pa", False)` (only `Maestro` has the property;
leads/vault default `False`). The builder then reads `cfg.is_pa` — a pure
function of its config, testable without patching `DEFAULT_MAESTRO`.

### 3. PA-vs-project identity block

In `loops.py`, beside `LOOP_PROMPTS` (existing precedent: short prompt text held
as Python constants in this same harness-neutral module):

```python
MAESTRO_IDENTITY: dict[str, str] = {
    "pa":      "You are the PA Maestro — Hive's default route … own NO project; "
               "read any project, write only ownerless ones …",
    "project": "You are a project maestro — you own exactly ONE project and "
               "write only within it …",
}
```

In `_build_pty_system_prompts`, when `cfg.role == "maestro"`, append
`MAESTRO_IDENTITY["pa" if cfg.is_pa else "project"]` after the role JD.

### 4. Neutralize `role-maestro.md`

Reword the opening so the shared JD assumes neither ownership state (the appended
block states which kind). Fix `:7` "Workers do the actual coding" →
current Leaf-agent / Workflow-run language. Narrow edits only — not a full JD audit.

## Alternatives considered (and rejected)

| Decision | Rejected option | Why rejected |
|----------|-----------------|--------------|
| Q1 source of truth | Re-derive `name == DEFAULT_MAESTRO` per call site | Worsens the "scattered, no single source of truth" problem the ticket exists to fix. |
| Q2 carry | Re-derive `cfg.name == DEFAULT_MAESTRO` inside the builder | Re-introduces the inline definition; couples the runtime adapter to a config constant; harder to unit-test. |
| Q3 strategy | Separate `role-pa.md` full JD | Duplicates a 215-line JD → drift on every shared-guidance edit; PA would never read `role-maestro.md`, so its framing stays wrong (fails acceptance). |
| Q3 strategy | Append an override block to an unchanged `role-maestro.md` | Self-contradicting prompt: base says "you own a project", block says "you don't". |
| Q3 text home | Two MD snippet files | Needs a loader change + two tiny files; `LOOP_PROMPTS` already establishes constants as the idiom for short prompt text. |
| Q4 dead code | Mirror new logic into `build_cli_args` | Propagating into a removed-headless-path corpse; no runtime effect. |
| Q4 dead code | Delete `build_cli_args` now | Touches 5 files + test rewrites; widens 033's blast radius before its live re-smoke. → its own cleanup ticket. |

## Why no ADR

Reversible (a property, a field, text constants, a reworded MD file — small diff
to undo), not surprising (self-explanatory + captured here), and the trade-off we
made was deliberately the *low-commitment* one. Two of three ADR criteria fail.
The ADR-worthy version would have refactored `load_role_jd` into a variant-aware
loader or unified the two prompt seams — we did neither. (The sprint's ADR
requirement attaches to **034**, not 033.)

## Multi-harness note (Phase 5 readiness)

When the Codex/OpenCode adapters land, each gets its own `*AdapterConfig` and
prompt builder. The reusable pieces are already harness-neutral: `Maestro.is_pa`
(source of truth) and `MAESTRO_IDENTITY` in `loops.py` (shared text). A future
adapter maps `entity.is_pa → cfg.is_pa` and appends `MAESTRO_IDENTITY` — nothing
to re-derive. `build_cli_args` is **not** that seam (it's the removed `claude -p`
path).

## Follow-up cleanup ticket (noted, not done here)

Delete the dead `entity.build_cli_args` + `Maestro.build_cli_args` (remnants of
the headless path removed in Ticket 007) and scrub their test-only call sites
(`test_entity.py`, `test_process_manager.py`, `test_vault.py`). Out of 033 scope.

## Out of scope (from ticket)

- Centralising every scattered PA reference (mapped in `research.md`; not refactored).
- Changing the write-fence (Ticket 024 / ADR 0017) — identity only.
- New PA capabilities or policy.
