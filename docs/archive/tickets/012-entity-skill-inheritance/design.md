# Design — Ticket 012: Curate & expose CC skills to Entities

Chosen approach for giving each Entity role a safe slice of the Claude
Code skill library. Grounded in [`research.md`](research.md); the
load-bearing decision is recorded in
[ADR 0008](../../adr/0008-per-role-skill-curation-denylist.md).

## Decision in one line

Block, per role, the skills that **pause for a human** — via a code-level
**denylist** fed into the existing `--disallowedTools` flag — and prompt
the role JDs to use the safe remainder. Everything autonomous (including
fan-out and side-effecting skills) stays reachable.

## Why a denylist, not the allowlist the ticket imagined

Claude Code's permission model only **hides** skills through `deny`
(`Skill(name)` → removed from the model's context). `allow` merely
*pre-approves*; it does not hide the unlisted ones, and under Hive's
`--dangerously-skip-permissions` the unlisted skills run regardless. A
literal "only these N" allowlist is therefore unachievable without the
fragile `CLAUDE_CONFIG_DIR` + curated-`skills/` route (undocumented,
partially broken, and built-ins leak through anyway). So we **exclude the
dangerous skills** and leave the rest — which still delivers the ticket's
hard requirement ("no human-interactive skill is reachable") and its own
"most, not all".

## The blocking criterion: does it stall on a human?

The single failure that actually breaks the fleet is a skill that **pauses
mid-Turn waiting for a human who isn't there** — the org deadlocks. That,
not "side-effects" or "fan-out", is the test.

```
  STALL  (block)                         NOT a stall  (allow)
  ─────────────────────────              ─────────────────────────────
  waits for a human: Q&A interview,      runs to completion autonomously,
  AskUserQuestion / plan gate,           even if it spawns helper agents
  STOP/approval checkpoint               or has side-effects
```

Two consequences make this role-dependent:

1. **The Maestro has a human (you).** Ticket 003's gate bridge turns an
   `AskUserQuestion`/plan gate into a **Telegram** approval row
   (`gate_coordinator.py` → `approval_handler` routes `approver="user"`
   to Telegram); plain Q&A rides normal Turn routing. So for the Maestro,
   *every* form of human-wait reaches you on Telegram — nothing forces you
   into the CC TUI. **A stalling skill is safe for the Maestro.**
2. **A Lead/Worker has no human.** Its wait escalates to a parent Entity
   (`_approver_for`: lead→maestro, worker→lead) that has no `/approve`
   action — so the Turn parks with nobody able to answer (deadlock), or
   floods you with sub-level questions (inverts the hierarchy). **A
   stalling skill must be blocked for Lead & Worker.**

We name a skill that pauses for a human a **thinking skill** (see
`CONTEXT.md`). Thinking skills are Maestro-only.

## Per-role policy

| Bucket | Maestro | Lead | Worker |
|--------|:------:|:----:|:------:|
| Autonomous (run to completion, incl. fan-out / side-effects) | ✅ | ✅ | ✅ |
| Thinking (pause for a human) | ✅ | ⛔ | ⛔ |
| Hands-on-interactive (`prototype` — needs you to drive a built app) | ⛔ | ⛔ | ⛔ |

Lead and Worker are identical for skill purposes. Only the Maestro adds
the thinking set.

## Final curation lists (exact `Skill()` deny tokens)

```
 DENY for ALL roles
   Skill(prototype)                  # builds a runnable app for you to drive — can't bridge to TG

 DENY for LEAD + WORKER only  (Maestro keeps these — its gates reach you)
   Skill(grill-me)        Skill(brainstorming)      Skill(grill-with-docs)
   Skill(improve-codebase-architecture)
   Skill(capture)         Skill(curate)             Skill(cc-freeze)
   Skill(triage)          Skill(plan-next-sprint)   Skill(run-ticket)
   Skill(initiate-project)
```

Everything not listed is **allowed** for every role — including
`build-with-agent-team`, `dispatching-parallel-agents`, `tdd-agentic`,
`git-ship`, `to-prd`, `to-issues`, `update-config`, `telegram:*`, and all
built-in CLI skills. (Deliberate: blocking those is theatre — an Entity
runs `--dangerously-skip-permissions` with full `Bash` and can do the same
by hand. `prototype` and `run-ticket` *grill*, so they're out on the stall
rule, not the fan-out rule.)

Source note: all 12 blocked skills are **user or plugin** skills, so all
are deniable. None is built-in. The one token to confirm at build time is
the plugin-namespaced `brainstorming` — `Skill(brainstorming)` vs
`Skill(superpowers:brainstorming)` — against the **pinned** fleet binary.

## Injection (mechanism A2)

A dedicated `src/hive/process/skill_curation.py` owns one role-keyed
mapping and a `skill_denylist_for(role) -> list[str]`; its tokens are
merged into `disallowed_tools` at `_adapter_config_from_entity()`
(`lifecycle_manager.py:109`). This reuses the proven
`entity.disallowed_tools → --disallowedTools` path
(`claude_adapter.py:87-88`), covers Workers (which have no `## Tools`
markdown section), keeps the curation list in one auditable place, and
sidesteps the whitespace-split fragility of the personality-markdown
parse (`entity.py:100-103`). The existing `## Tools` block is untouched.

## JD prompting

Each role JD gains a `## Skills — when to use` section (after the
messaging/org sections, before Honesty). Worker/Lead JDs point at the
autonomous set (`tdd`, `diagnose`, `research-codebase`,
`systematic-debugging`, `requesting-code-review`…); the Maestro JD adds
the thinking set (`grill-me`, `brainstorming`…) for clarifying goals with
you. JDs are loaded via `load_role_jd` (`process/loops.py`), so this is a
content-only edit to `personalities/role-{maestro,lead,worker}.md`.

## Alternatives rejected

| Option | Why not |
|--------|---------|
| True **allowlist** ("only these N") | Not supported — `allow` doesn't hide unlisted skills; needs the fragile `CLAUDE_CONFIG_DIR` route |
| **Global** denylist (same for all) | Loses the Maestro's legitimate "grill the human" capability; the Maestro's gates reach you |
| `--settings` `permissions.deny` JSON | Richer (prefix globs) but net-new flag path; we only need exact names |
| Personality `## Tools` markdown line | Whitespace-split mangles multi-token entries; Workers have no such section |
| Per-spawn **config-dir isolation** | Expensive + relies on broken `CLAUDE_CONFIG_DIR`; built-ins persist anyway |
| Blocking fan-out / sensitive skills | Theatre — Entities run yolo+Bash and can do the same by hand |

## Side effects (declared)

- **[ADR 0008]** — records the denylist-not-allowlist + stall-criterion
  decision (surprising, hard-ish to reverse, a real trade-off).
- **`CONTEXT.md`** — new glossary term **"thinking skill"**.
- A **follow-up Ticket 014** trims the VPS skill library at the filesystem
  level (global removal — the complement to this per-role denylist).
  Revisit `skill_curation.py` after that trim to drop now-redundant tokens.

## Verification

- **CI (hermetic):** a unit test on the mocked-PTY/Fake-adapter seam
  (Ticket 010) asserting each role's spawn args carry the expected
  `Skill(...)` deny tokens — and that the Maestro's do **not** carry the
  thinking-skill tokens.
- **Build-time check:** confirm the `brainstorming` deny-token form on the
  pinned binary.
- **Host smoke:** spawn a real Worker on the deployed code; confirm a
  blocked skill (e.g. `grill-me`) is absent and an allowed one (e.g.
  `tdd`) invokes.

## Residual / accepted

- **Denylist rot:** a newly installed skill auto-allows until added to the
  list. Accepted for a personal fleet; Ticket 014's filesystem trim is the
  other half of the lever.
- **Built-in CLI skills** (`loop`, `schedule`, `agent-teams`, …) are
  un-deniable but all in the allow set anyway — no exposure.
