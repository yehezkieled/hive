# Research — Ticket 014: Trim the VPS skill library

> **Reconciliation note.** A prior `research.md` (inventory of **31** user
> skills) was stranded on branch `rescue/ticket-014-research`, never merged.
> This file **merges** its still-valid analysis with the **current** VPS
> state and corrects one claim that the prior file got wrong in hindsight
> (the trim ∩ deny = ∅ "no-op", below). The rescue branch is superseded by
> this file and can be deleted.

## Method

Inventoried every user skill under `~/.claude/skills` on the VPS, the
installed plugins (`~/.claude/plugins/installed_plugins.json`), and the
slash-command set (`~/.claude/commands`). Classified each against two
questions:

1. **Does any actor need it?** — the developer (interactive CC sessions) *or*
   any Hive Entity (Maestro / Lead / Worker spawned on the same global
   `~/.claude`).
2. **Is it Hive-relevant, or dead weight for an unused stack/workflow?**

Cross-checked "is it used" against `src/`, `docs/`, and
`process/skill_curation.py` (the Ticket 012 per-role denylist).

## Headline finding: the filesystem trim already happened

The ticket assumes a fat library ("exercise scaffolding, article editing,
course tooling, migration helpers"). The rescue-branch research inventoried
**31** user skills. The live VPS today has **18**, and every one is Hive- or
dev-relevant. A manual pass already removed the dead weight — and went
**further** than the prior research recommended:

```
  rescue research (31)                       live ~/.claude/skills (18)
  ─────────────────────                      ──────────────────────────
  TRIM x4  edit-article, scaffold-exercises, ─► all 4 GONE ✓ (as recommended)
           migrate-to-shoehorn, setup-pre-commit
  DECIDE   caveman, zoom-out ──────────────► both GONE
           build-with-agent-team ──────────► STILL PRESENT (only survivor)
  KEEP x7  git-guardrails-claude-code, grill-me,
           prototype, remind, setup-matt-pocock-skills,
           tdd, triage ───────────────────► ALL 7 ALSO GONE (trim exceeded rec)
  KEEP x17 capture, cc-freeze, curate, …    ─► all present ✓
```

So acceptance #2 ("the agreed skills removed from the VPS") is **already
satisfied on disk** for the bulk of the library. What remains is one more
trim (`build-with-agent-team`, decided below) and the denylist fallout.

## The two layers — and where they NOW overlap (correcting the rescue file)

[ADR 0008](../../adr/0008-per-role-skill-curation-denylist.md) /
[Ticket 012](../012-entity-skill-inheritance/) curate **per role** with a
denylist keyed on **liveness** — block a skill iff it *stalls mid-Turn for a
human* (a "thinking skill"). Ticket 014 deletes on a **different**
criterion: *no actor needs it at all*.

The rescue research claimed these two sets are disjoint, so acceptance #3
("prune deny tokens for deleted skills") was a **confirmed no-op**. That was
true for its *recommended* 4-skill trim. **It is false for the trim that
actually happened**, which removed three skills that the denylist names:

```
  skill_curation.py token        skill on disk?   →  status
  ───────────────────────        ──────────────       ──────────────────────
  _ALL_ROLES_DENY
    Skill(prototype)             ✗ gone               DORMANT (dead) token
  _THINKING_DENY
    Skill(grill-me)              ✗ gone               DORMANT (dead) token
    Skill(triage)                ✗ gone               DORMANT (dead) token
    Skill(brainstorming)         ✓ via plugin         LIVE — token form unverified*
    Skill(grill-with-docs)       ✓ present            live, correct
    Skill(improve-codebase-architecture) ✓ present    live, correct
    Skill(capture)               ✓ present            live, correct
    Skill(curate)                ✓ present            live, correct
    Skill(cc-freeze)             ✓ present            live, correct
    Skill(plan-next-sprint)      ✓ present            live, correct
    Skill(run-ticket)            ✓ present            live, correct
    Skill(initiate-project)      ✓ present            live, correct
```

So acceptance #3 is **real work, not a no-op**: three tokens point at skills
that no longer exist. (Decision on what to do with them below.)

\* **brainstorming token form** — flagged in 012's
[`design.md`](../012-entity-skill-inheritance/design.md) to confirm at build
time. `brainstorming` is not a `~/.claude/skills` entry; it ships from the
**superpowers plugin**, so Claude Code may address it as
`superpowers:brainstorming`. The denylist has the **bare** `Skill(brainstorming)`.
If the bare form does not deny a plugin-namespaced skill, a Lead/Worker can
still invoke `brainstorming`, hit its interactive pause, and **deadlock** —
the exact failure 012 exists to prevent. Pinned fleet binary = **2.1.170**
(`~/.local/bin/claude`); this host = 2.1.177.

## The four states a skill can be in (the mental model)

The denylist only gates **Entities**; the developer's own interactive CC
always sees the full on-disk library.

```
                          on disk?   token in denylist?   →  reachable by
  (A) deleted              ✗          (token is dormant)   →  nobody — not even dev
  (B) on disk, no token    ✓          —                    →  dev + every entity
  (C) on disk, _THINKING   ✓          deny (all but maestro)→  dev + maestro only
  (D) on disk, _ALL_ROLES  ✓          deny (all roles)     →  dev only — no entity
```

"In my CC but never an entity" = **state (D)**. The three dead-token skills
are currently in **state (A)**.

## Decisions taken (developer, this run)

1. **`build-with-agent-team` → TRIM.** The last "DECIDE" survivor. It is
   tmux Agent-Teams orchestration; S5 made the **Workflow** tool Hive's
   leaf fan-out primitive ([ADR 0010](../../adr/0010-leads-orchestrate-via-workflow.md)),
   so it is superseded. Not referenced in `src/`/`tests/` (pure
   `~/.claude/skills` entry). ADR 0008 line 44 names it as an example
   allowed-fan-out skill — **left untouched** (ADRs are append-only;
   the example is historical, the reasoning still stands).
2. **Three dormant tokens (`prototype`, `grill-me`, `triage`) → KEEP +
   ANNOTATE.** Denying a non-existent skill is a harmless no-op, so keeping
   them costs nothing and **re-guards** the skill automatically if it is ever
   reinstalled: `prototype` → state (D) dev-only; `grill-me`/`triage` →
   state (C) dev+maestro. This preserves the "mine-only, never an entity"
   capability for free. Acceptance #3 is met by **review + retain with a
   `# not installed — dormant guard` comment** rather than deletion, keeping
   the file auditable ("kept tidy") without discarding protection.
3. **brainstorming token form → fold into 014.** Verify on the pinned
   binary whether `Skill(brainstorming)` actually denies the plugin skill;
   correct to `Skill(superpowers:brainstorming)` if the bare form is dead.
   In-scope because it is the same file and a silently-broken guard is a live
   stall risk.

## Plugin & command halves — nothing to remove

`installed_plugins.json` lists four plugins, all in active use:

| Plugin | Status | Why kept |
|--------|--------|----------|
| telegram | in use | Hive's entire control surface. |
| superpowers | in use | Default workflow method (CLAUDE.md). |
| context7 | in use | Live library-docs lookup. |
| claude-hud | in use | Statusline. |

No disabled/orphaned plugins → the "unused plugin skills" clause has no
target. `~/.claude/commands` (`agent-teams`, `btr`, `kickoff`, `pao`,
`ralph`, `yolo-loop`) are dev loop commands, out of scope (not skills; not
inherited as `Skill()` tools). The `agent-teams` *command* is distinct from
the `build-with-agent-team` *skill* — trimming the latter does not touch it.

## Recommendation → plan.md

- Trim **`build-with-agent-team`** from `~/.claude/skills` (VPS file op).
- In `skill_curation.py`: annotate the three dormant tokens; verify & fix
  the `brainstorming` token form.
- Verify: `ruff` + `pytest -m "not integration"`; fleet spawns; a maestro
  Turn completes; `brainstorming` is genuinely unreachable for a Lead.
- The trim set is **disjoint from any live deny token** (the dormant three
  are retained by choice), so no live guard is stranded.
