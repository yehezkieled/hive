# Design — Ticket 044: Pattern-library awareness

> One-shape ticket: no standalone `outline.md` — the single edit is fully
> specified below. DIRECT lane (one PR). Decision recorded in
> **ADR 0025** (amends [ADR 0021](../../adr/0021-further-patterns-as-global-skills.md)).

## Chosen approach

Replace the stale closing sentence of the `## Interaction patterns` **intro**
in `personalities/role-lead.md` with a ~5-line awareness pointer. The pointer
tells the Lead three things: (1) further coordination shapes live in its
inherited global-skill library, not as recipes in this file; (2) it
**self-selects** when a fan-out matches a known shape; (3) the maestro need not
name them. The `### debate` recipe stays byte-for-byte untouched.

**Why replace, not append:** the existing sentence — "Today one pattern is
defined; more arrive on the same mechanism" — is wrong under ADR 0021 (further
patterns arrive as global *skills*, not on the JD-recipe mechanism). Leaving it
in place beside the new pointer would contradict the pointer.

**Why role-only, no maestro change:** ADR 0021 drops the
maestro-names-the-pattern path for the *further* (global-skill) patterns; the
developer wants the Lead to self-select, serving the loop-engineering aim (the
loop self-organizes fan-outs without human dictation). The maestro keeps its
menu only for the inline `debate` recipe.

## The edit — concrete before / after

`personalities/role-lead.md`, end of the `## Interaction patterns` intro
(currently lines 82-85).

**Before:**

```
name a pattern in the contract ("use the `debate` pattern"), or you may
choose one yourself. Today one pattern is defined; more arrive on the
same mechanism.
```

**After:**

```
name a pattern in the contract ("use the `debate` pattern"), or you may
choose one yourself.

**More patterns live in your skills, not here.** Beyond the `debate`
recipe below, further coordination shapes — e.g. `blackboard` (agents
co-edit one shared artifact) and `tournament` (candidates pruned over
rounds) — ship as **global skills** in the Claude Code skill library you
inherit (see "Skills — when to use" below), not as recipes in this file.
When a fan-out matches a known shape, scan your skills and reach for one —
you **self-select**; your maestro need not name it. `debate` is the single
shape embedded inline here.
```

Net: the inline `debate` recipe is the floor; everything beyond it is a
self-selected skill. No code, no engine change — the JD reaches the Lead via
`load_role_jd("lead")` → `--append-system-prompt` (see `research.md`).

## Test

Add to `tests/test_role_jd.py` → `TestRepoLevelRoleFiles`, mirroring
`test_lead_jd_documents_debate_pattern`:

```python
def test_lead_jd_points_to_global_pattern_skills(self) -> None:
    """Ticket 044 / ADR 0025: the lead JD makes the Lead AWARE that further
    interaction patterns ship as inherited global skills (not JD recipes),
    and that the Lead self-selects one — without the maestro naming it.
    """
    repo_root = Path(__file__).parent.parent
    flat = " ".join(
        (repo_root / "personalities" / "role-lead.md").read_text().split()
    )
    assert "global skills" in flat          # where further patterns live
    assert "self-select" in flat            # the Lead chooses, not the maestro
    assert "skill library you inherit" in flat
    # the stale "same mechanism" promise is gone
    assert "more arrive on the same mechanism" not in flat
    # the debate recipe is undisturbed (regression guard)
    assert "blind to each other" in flat
```

The existing `test_lead_jd_documents_debate_pattern` continues to guard that
the recipe survives the edit.

## Side effects

- **ADR 0025** (new, amends 0021): the awareness-pointer decision +
  self-select / no-maestro-dictation. Written by this run-ticket pass and
  shipped ahead of the build.
- **CONTEXT.md:** no change — the "Interaction pattern" glossary entry already
  states further patterns ship as user-authored global skills.
- **role-maestro.md:** no change (non-goal).
- **Skill-curation denylist:** no change — interaction-pattern skills already
  auto-allow for Leads.

## Alternatives considered

- **Append the pointer, keep the old sentence** — rejected: the old sentence is
  now false (ADR 0021), it would contradict the pointer.
- **Add a matching maestro pointer** — rejected: the developer wants the Lead
  to self-select these; keeps the asymmetric-depth model (ADR 0020) simple.
- **Source skill files in-repo + install-at-deploy** so the menu is never
  dangling — rejected for this personal-project scope (ADR 0021 leans against
  it); the developer owns the global library directly.
