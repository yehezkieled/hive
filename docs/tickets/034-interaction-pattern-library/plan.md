# Plan — Ticket 034: interaction-pattern delivery mechanism + `debate`  (issue #188)

**Lane:** direct — one cohesive PR. Mechanism is JD recipes (ADR 0020), so the
build is markdown + tests; no engine code. ADR 0020 and the `CONTEXT.md` glossary
entries already shipped with `design.md`; the build PR only touches the two role
files + tests and closes #188.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `personalities/role-lead.md` | modify | Add `## Interaction patterns` (full `debate` recipe + embedded Workflow skeleton + result shape) between "Authoring rules" and "What you do NOT do". |
| `personalities/role-maestro.md` | modify | Add a short "delegate a pattern by name" menu near the Spawn Template (names + one-line when-to-use; maestro names, lead runs). |
| `tests/process/test_interaction_patterns.py` | create | Prompt-assembly tests: lead prompt contains the full `debate` recipe; maestro prompt contains the menu but **not** the skeleton (asymmetric depth); `_MAESTRO_DENY` still contains `Workflow`. |
| `docs/adr/0020-interaction-patterns-as-jd-recipes.md` | (already shipped) | Decision — mechanism only. |
| `CONTEXT.md` | (already shipped) | Glossary: **Interaction pattern** + **debate**. |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` green
- Behaviour-change rule (S6/S7): deployed re-smoke — spawn a lead, confirm via
  its transcript that the `debate` recipe is in its system prompt; ideally drive
  one real `debate` run end-to-end and eyeball the verdict shape.

## Out of scope

- `blackboard` (035) / `tournament` (036) / `research-consolidate` — S8, on this
  same mechanism.
- Rebuttal (multi-round) debate; mid-run Workflow steering (ADR 0014).
- Any change to the Workflow engine or to skill curation / tool policy
  (`Workflow` stays in `_MAESTRO_DENY`, unchanged).

## Cross-cutting impact

- **Reference docs:** none beyond the glossary entry already landed. (`CONTEXT.md`
  is glossary-only; no README/DEPLOYMENT/ARCHITECTURE change.)
- **Role-file proximity:** `role-maestro.md` is also edited by Ticket **033** (PA
  self-identity) — whichever PR lands second rebases (flagged in the S7 sprint).
- **ADR number:** `0020` confirmed free vs `origin/main` at planning time;
  re-confirm at merge (parallel S7 worktrees can race it).

## Build handoff

Direct lane — build as **one PR that closes #188**: a single branch editing the
two role files + adding the test, gated by the verification commands above.
