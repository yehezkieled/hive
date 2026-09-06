# Design — Ticket 034: interaction-pattern delivery mechanism + `debate`

Chosen approach for **how** a reusable interaction pattern reaches a Team Lead,
plus the definition of the first pattern, `debate`. Decisions settled in the
design grill; evidence in `research.md`.

## Decision summary

| # | Decision | Rationale (short) |
|---|----------|-------------------|
| D1 | **Mechanism = JD push.** Patterns ship as **role-file content** (`personalities/role-*.md`), injected on every spawn via `load_role_jd()`. | The only delivery path with a **spawn-time guarantee** — in-repo, version-controlled, cwd-independent, unit-testable. Skills/saved-scripts are unprovisioned and cwd-fragile (`research.md` §1, §4). |
| D2 | **Shared vocabulary, asymmetric depth.** Lead gets the full executable **recipe** (`role-lead.md`); maestro gets a **menu** — names + one-line "when to use" (`role-maestro.md`). | A named library is only "shared" if both sides know the names. The maestro writes the contract, so it must be able to *name* a pattern; the lead executes, so it needs the full shape. |
| D3 | **Execution Lead-only — unchanged.** Maestro *names* a pattern; only the lead *runs* it. | `Workflow` is already in `_MAESTRO_DENY` (`tool_policy.py:47`): a maestro cannot drive a run. So Lead-only execution is free — no denylist change, the Maestro→Lead→Workflow chain holds. |
| D4 | **ADR 0020 fixes the mechanism only**, not `debate`'s shape. | Precedent: ADR 0010 decided *"use Workflow,"* not the use-case shapes. `debate`'s semantics are a definition (`CONTEXT.md` + `role-lead.md`), not a hard-to-reverse decision. |
| D5 | **Direct lane** — one cohesive PR. | ADR + two role-file edits + one glossary entry + tests = a single shippable unit, not 2+ independently-mergeable slices. |

## The mechanism (D1–D3)

A pattern is a **named, canonical coordination recipe** for a Lead's Workflow
fan-out, delivered as prose + a Workflow-script skeleton inside the role files
that are already pushed into every Entity's system prompt at spawn.

```
  spawn(lead)  ──load_role_jd("lead")──▶  role-lead.md  ──▶  full recipe in prompt
  spawn(maestro)──load_role_jd("maestro")▶ role-maestro.md ─▶  menu in prompt
                                                              │
  maestro contract: "...; use the `debate` pattern" ──────────┘ (names it)
        │
        ▼
  lead recognizes a debate-shaped problem → authors the canonical Workflow → runs it
```

**Important honesty:** this adds *no* engine capability — a Lead can already
author parallel-agents-plus-judge with the Workflow tool today. 034's value is
**consistency** (every Lead runs the shape the same, known-good way), **shared
vocabulary** (the maestro can ask for it by name), and a **foundation** to
replicate `blackboard`/`tournament` cheaply in S8. That is exactly why the
mechanism is *instructions*, not code — there is nothing to build in the engine.

## `debate` semantics (the first pattern)

**When to use:** a decision/judgment over a wide solution space, or a claim
needing adversarial scrutiny — "which of these N options," "is this finding
real," "should we commit to X." Distinct from `blackboard` (agents collaborate
on a shared evolving artifact) and `tournament` (many candidates pruned in
rounds). `debate` = **independent opposing answers, then one adjudication.**

**Shape — one round, one answer per agent:**

1. The Lead enumerates N answers/sides for the topic.
2. **N debater agents run in `parallel()`, blind to each other**, each making the
   strongest case for its assigned answer. Independence is the point — it
   prevents groupthink and produces genuinely opposed cases.
3. **One judge agent** reads all cases (barrier — it needs them all), weighs the
   trade-offs, and returns a verdict with reasons.

**Result shape:**

```json
{
  "topic": "string",
  "positions": [{ "stance": "string", "argument": "string" }],
  "verdict":   { "choice": "string", "rationale": "string", "dissent": "string|null" }
}
```

**Variations (documented, not separately built):**
- *Adversarial-verify* is the 2-side special case: "this claim is true" vs
  "false" → judge rules. Covers the "is this bug real?" use.
- *Rebuttal rounds* (debaters see each other's openings, then respond) are an
  **S8 extension**, deliberately out of scope here — the ticket proves the
  mechanism on a minimal pattern (ADR 0014 keeps runs single-Turn/deterministic).

## Side effects (declared upfront — cross-cutting)

- **New decision:** `docs/adr/0020-interaction-patterns-as-jd-recipes.md`
  (mechanism only). ⚠ Re-confirm `0020` is still free at ship time — parallel S7
  worktrees (032/033) can race the number.
- **Glossary:** `CONTEXT.md` gains an **Interaction pattern** entry (parent) and
  a **debate** entry, using canonical terms (Workflow run, Leaf agent).
- **Role files (the implementation, built in the PR that closes the issue):**
  `role-lead.md` gains a `## Interaction patterns` section (full `debate` recipe,
  inserted between "Authoring rules" and "What you do NOT do"); `role-maestro.md`
  gains a short "delegate a pattern by name" menu near the Spawn Template.

## Alternatives rejected

- **Per-pattern skill / routing skill (B/C).** Same *content* as JD, but no
  spawn-time delivery guarantee: unprovisioned (Hive ships no skills, inherits
  global `~/.claude`), cwd-fragile from a Lead's per-project worktree, and
  exposed to denylist rot. Building a provisioning pipeline is the engine work
  the ticket excludes.
- **Saved Workflow script `.claude/workflows/*.js` (D).** Same provisioning/cwd
  problems; and no "load saved pattern and run" hook exists in Hive — Leads
  author scripts inline. Its value (a reusable skeleton) is captured *inside* the
  JD recipe instead.
- **Maestro-executable patterns.** Would require lifting `Workflow` from
  `_MAESTRO_DENY`, collapsing the Maestro→Lead→Workflow chain (ADR 0010).
  Rejected — maestro names, lead runs.
