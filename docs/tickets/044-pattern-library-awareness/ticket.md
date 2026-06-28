# 044 — Pattern-library awareness (global skills + thin JD pointer)

> Opens the S9 **backend / loop-engineering** track. Amends
> [ADR 0021](../../adr/0021-further-patterns-as-global-skills.md). The
> interaction-pattern **skill files** are user-authored global skills
> (`~/.claude/skills`) — **out of this ticket's scope.** This ticket only makes a
> Team Lead *aware* the library exists so it self-selects.

## What

Add a thin (~5-line) "menu" awareness pointer to `personalities/role-lead.md` so a
Team Lead knows it has a library of interaction-pattern **skills** beyond the
embedded `debate` recipe (Ticket 034), and reaches for one when the work fits a
known coordination shape. Record the decision in a **new ADR** (next free number)
amending [ADR 0021](../../adr/0021-further-patterns-as-global-skills.md).

## Why

Per [ADR 0021](../../adr/0021-further-patterns-as-global-skills.md), further
coordination patterns ship as **user-authored global skills**, inherited by Leads
via Ticket 012 / ADR 0008 — and the plumbing **already works**: global
`~/.claude/skills` are inherited wholesale, and interaction-pattern skills are not
on the curation denylist (they're autonomous executor skills, so they auto-allow
for Leads). The one real gap is **awareness**: a Lead won't reach for a skill it
doesn't know exists. A ~5-line JD pointer closes that gap with **zero engine
work** — serving the loop-engineering aim (the loop self-organizes its fan-outs
without the human dictating). This also **drops ADR 0020's maestro-names-the-
pattern path** — the developer does not want to dictate pattern selection.

## Acceptance

- `personalities/role-lead.md` gains a concise pattern-library pointer (the Lead
  is told the global skill library exists + when to reach for one), inserted
  alongside the existing `debate` recipe without disturbing it.
- A new ADR (next free number, amending 0021) records: awareness pointer in the
  JD; patterns stay user-authored global skills; the Lead self-selects (no
  maestro dictation).
- A prompt-assembly test asserts the pointer reaches a spawned Lead's role JD.
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- Authoring the interaction-pattern skill files themselves (user-authored in
  `~/.claude/skills`).
- Sourcing skill files in-repo + install-at-deploy (a separate decision; ADR 0021
  leans against it for this personal-project scope).
- Any change to the skill-curation denylist (interaction-pattern skills already
  auto-allow for Leads) or to the maestro role file.

## Notes / open

- Insertion point: `personalities/role-lead.md`, after the pattern intro, before
  the `### debate` recipe. Loader path: `process/loops.py` (`load_role_jd`) →
  `runtime/claude_adapter.py` (`_build_pty_system_prompts`) → `--append-system-
  prompt`.
- **ADR number races across parallel worktrees** — re-check the next free number
  at ship time (currently ~0025) and fix refs before merge.
- Size: tiny (JD pointer + ADR + one prompt-assembly test).
