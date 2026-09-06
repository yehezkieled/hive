# Research — Ticket 044: Pattern-library awareness

> Trivial ticket: no standalone `questions.md` — the few unknowns are
> resolved inline below. Findings carry real file refs from a code read on
> 2026-06-28.

## The gap is awareness, not plumbing

The interaction-pattern **skill library** already reaches Leads. Verified:

- **Global-skill inheritance is wholesale.** Leads inherit the Claude Code
  global skill library (`~/.claude/skills`) via Ticket 012 / ADR 0008, gated
  only by the per-role curation denylist. Interaction-pattern skills are
  *autonomous executor* skills (they run a Workflow fan-out, they don't pause
  for a human), so they are **not** on the denylist → they auto-allow for a
  Lead. Confirmed by the existing `## Skills — when to use` block in
  `personalities/role-lead.md:174-195`, which already tells the Lead it
  inherits the global library.
- **ADR 0021** (`docs/adr/0021-further-patterns-as-global-skills.md`) is the
  decision of record: further coordination shapes ship as user-authored global
  skills, *not* Hive-native JD recipes — "the provisioning gap 0020 cited does
  not exist for global skills."

So the only missing piece is **awareness**: a Lead won't invoke a skill it
doesn't know exists. Nothing in the role JD points at the pattern-skill library.

## Injection path (how a JD edit reaches a spawned Lead)

`personalities/role-lead.md`
  → `load_role_jd("lead")` — `src/hive/process/loops.py:15`
  → appended via `--append-system-prompt`:
      - `src/hive/runtime/claude_adapter.py:111-117` (`_build_pty_system_prompts`)
      - `src/hive/models/entity.py:322,343`

An edit to `role-lead.md` is therefore live in every spawned Lead's system
prompt with zero engine work. (Read cache: `_read_role_file` is `lru_cache`d —
static within a process lifetime, irrelevant to a content change shipped on
disk.)

## Where the pointer goes — and a stale line to fix

`role-lead.md` already has a `## Interaction patterns` section
(`role-lead.md:76-119`): an intro (76-85) followed by the `### debate` recipe
(87-119). The intro's closing sentence is now **stale**:

> "Today one pattern is defined; more arrive on the same mechanism."
> — `role-lead.md:83-85`

Under ADR 0021 further patterns do **not** arrive "on the same mechanism" (JD
recipes) — they arrive as global skills. The awareness pointer should *replace*
this sentence, not sit beside it. The `### debate` recipe itself stays
untouched (acceptance requirement).

## Test pattern to mirror

`tests/test_role_jd.py` → class `TestRepoLevelRoleFiles` reads the on-disk role
file, normalises whitespace (`" ".join(text.split())`) and asserts substrings.
`test_lead_jd_documents_debate_pattern` (`tests/test_role_jd.py:231`) is the
exact model for the new awareness assertion.

## Questions resolved (folded from `questions.md`)

- **Q: Does global-skill inheritance actually reach Leads today?**
  A: Yes — wholesale via 012/0008; interaction-pattern skills auto-allow
  (autonomous, not denylisted).
- **Q: Exact insertion point?**
  A: `role-lead.md` `## Interaction patterns` intro — replace the stale
  "more arrive on the same mechanism" sentence; leave `### debate` alone.
- **Q: Does the maestro role file need a matching pointer?**
  A: **No** (non-goal). ADR 0021 drops the maestro-names-the-pattern path for
  *further* patterns; the Lead self-selects. The maestro keeps only the menu
  for the inline `debate` recipe (guarded by
  `test_maestro_jd_lists_pattern_menu_not_recipe`).
- **Q: Does `CONTEXT.md` need a glossary change?**
  A: No — the "Interaction pattern" entry already states further patterns ship
  as user-authored global skills (ADR 0021). No new term is introduced.
