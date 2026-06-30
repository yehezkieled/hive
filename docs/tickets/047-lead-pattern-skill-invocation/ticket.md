# 047 — Strengthen the lead JD pattern pointer to drive skill *invocation*, not just shape-awareness

> Deferred follow-up to [044](../044-pattern-library-awareness/). **Not committed
> to a sprint** — slot during a future sprint-planning pass (fits the
> loop-engineering direction in [`../../roadmap.md`](../../roadmap.md)). Small.
> Backed by a live behavioral observation (single data point — see Notes).

## What

Ticket 044 added an awareness pointer to `personalities/role-lead.md` telling a
Lead that further coordination shapes ship as inherited **global skills**, and to
*"scan your skills and reach for one."* The 2026-06-29 behavioral test showed the
pointer makes a Lead **shape-aware and self-selecting** — but **not
skill-invoking**: given an unnamed coordination task, the Lead named `split` and
**hand-authored** the Workflow directly, with **0 calls to the Skill tool**. It
never opened the `/split` skill.

This ticket strengthens the JD wording (and any surrounding framing) so that when
a fan-out matches a known shape, the Lead **invokes the matching skill** — getting
the skill's refined, tested recipe — rather than reconstructing the shape from its
name alone.

## Why it matters

```
044 delivered:   awareness ✅  →  shape-selection ✅  →  skill invocation ❌  ← this ticket
```

The value of the global pattern skills is their *refined recipes*. For a trivial
fan-out (the test was a 4-file survey) hand-rolling "split" is fine. But for the
richer shapes — `double-check`'s majority-vote adversarial verify, `sweep`'s
loop-until-dry, `compete`'s judge panel — a Lead that only borrows the shape name
and improvises will miss exactly the refinements the skill encodes. Awareness
without invocation leaves most of the library's value on the table, and works
against the loop-engineering aim (the loop self-organizing *well*, not just
self-organizing).

## Proposed direction (to be confirmed in design)

- Reword the pointer from *"scan your skills and reach for one"* toward
  *"**invoke** the matching `/skill` when a fan-out fits a known shape"* — explicit
  about running the skill, not borrowing the idea.
- Resolve the likely root confusion: the inline **`debate`** recipe is an embedded
  pattern the Lead authors itself (not a skill). That sets an implicit "patterns =
  author them yourself" precedent that may bleed into the global-skill patterns.
  Decide whether to (a) distinguish in the JD between the one *embedded* recipe and
  the *invoke-the-skill* patterns, or (b) converge `debate` to a skill too for
  consistency. (Design call — likely (a) for this ticket.)

## Acceptance

- The lead JD wording drives **skill invocation** (not just awareness) when a
  shape fits — without removing the Lead's freedom to author free-form when no
  skill matches.
- A behavioral re-test on a **non-trivial** coordination task (one where a skill's
  recipe adds real value, e.g. `double-check`/`sweep`) shows the Lead **invoking
  the Skill tool** (a `Skill` call present in its transcript), not hand-authoring.
- 044's awareness/self-select framing and the inline `debate` recipe are not
  broken.
- Prompt-assembly test updated/added; `ruff` + `pytest -m "not integration"` green.

## Non-goals

- Authoring or changing the skill files themselves (user-authored global skills).
- **Forcing** invocation — the Lead must still author free-form when no skill
  fits; this is "invoke when one fits," not "always invoke."
- Any `role-maestro.md` change.

## Notes / open

- **Evidence:** 2026-06-29 live test — Maestro `otter` spawned Lead
  `otter.envscan` for a read-only env-var survey with **no pattern named in the
  contract**; the Lead self-selected `split` and authored its own Workflow
  (`meta.name: env-var-survey`, one `parallel()` fan-out), **0 Skill calls**.
  Captured in auto-memory `project_hive_044_pointer_awareness_not_invocation`.
- **Single-observation caveat:** one simple task. Before a heavy reword, reproduce
  the gap on a harder task (the design's re-test doubles as that confirmation) — it
  may be that Leads *do* invoke skills when the shape is non-trivial enough that
  improvising is visibly harder.
- **Adjacent (out of scope, note only):** during the same test the maestro↔lead
  *confirm* handshake stalled — the Lead parked for the maestro's OK and the
  maestro wasn't poked to process the mailbox message for ~3 min (long scheduler
  interval); a user nudge unstuck it. Possible poke-latency gap in the
  lead→maestro→confirm loop — worth its own ticket if it recurs.
