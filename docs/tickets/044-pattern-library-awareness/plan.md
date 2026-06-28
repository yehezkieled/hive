# Plan — Ticket 044: Pattern-library awareness  (issue #222)

DIRECT lane: one PR. Adds a ~5-line awareness pointer to the Lead role JD plus
one prompt-assembly test. The decision **ADR 0025** and the planning artifacts
ship ahead of the build (via run-ticket); this build PR is the implementation
that closes #222. Exact pointer text + test body live in
[`design.md`](design.md).

## Files this Ticket creates / modifies
| Path | Op | Step |
|------|----|------|
| `personalities/role-lead.md` | modify | In the `## Interaction patterns` **intro**, replace the stale "Today one pattern is defined; more arrive on the same mechanism." sentence with the ~5-line awareness pointer (verbatim text in `design.md` §The edit). **Do not touch `### debate`.** |
| `tests/test_role_jd.py` | modify | Add `test_lead_jd_points_to_global_pattern_skills` to `TestRepoLevelRoleFiles` (body in `design.md` §Test): assert `global skills` / `self-select` / `skill library you inherit` reach the JD, the stale sentence is gone, and `debate` survives. |

## Verification
- `pytest tests/test_role_jd.py -q` — new test green; `test_lead_jd_documents_debate_pattern` still green (recipe undisturbed).
- `pytest -m "not integration" -q` — full unit suite green.
- `ruff check src/ tests/ && ruff format --check src/ tests/` — both gates clean.
- Spot check: `load_role_jd("lead")` output contains the pointer (the new test asserts this).

## Out of scope
- Authoring the global skill files (`~/.claude/skills`) — user-authored.
- Any `role-maestro.md` change; any skill-curation denylist change.
- In-repo skill sourcing / install-at-deploy.

## Cross-cutting impact
- **ADR 0025** (`docs/adr/0025-lead-pattern-library-awareness-pointer.md`) — amends 0021; written + shipped by run-ticket ahead of this build.
- **CONTEXT.md** — no change (the "Interaction pattern" glossary entry already states further patterns ship as user-authored global skills).
