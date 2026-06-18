# Outline — Ticket 034: interaction-pattern delivery mechanism + `debate`

Implementation structure for the build PR (the one that closes the issue). The
mechanism is **JD recipes** (ADR 0020), so the work is markdown + tests — no
engine code. Steps are ordered; each is small.

## 1. `personalities/role-lead.md` — the full `debate` recipe

Insert a new `## Interaction patterns` section **between** "Authoring rules"
(ends ~line 74) and "## What you do NOT do" (~line 76). Contents:

- A 2–3 line intro: what an interaction pattern is, that it's a *named* shape for
  the Workflow you already author, and that you reach for one when the work fits.
- `### debate` subsection:
  - **When to use** (wide-solution-space decision / claim needing scrutiny;
    contrast blackboard & tournament in one line each).
  - **Shape:** one round, one answer per agent; debaters run in `parallel()`
    **blind to each other**; one judge reads all and decides with reasons.
  - **The skeleton** — a `js` code block the Lead copy-adapts (the embedded
    "template"), using `parallel()` for the N debaters + a single `agent()` judge,
    each `schema`-shaped per the Authoring rules (distilled results, no raw dump):

    ```js
    // debate: independent answers, then a judge picks one
    const sides = [/* the answers/options for the topic */]
    const cases = await parallel(sides.map(s => () =>
      agent(`Make the strongest case for ${s}. Ignore the other options.`,
            { schema: CASE })))
    const verdict = await agent(
      `Cases: ${JSON.stringify(cases)}. Pick the best and explain why.`,
      { schema: VERDICT })
    return { topic, positions: cases, verdict }
    ```
  - **Result shape:** `{ topic, positions:[{stance,argument}], verdict:{choice,rationale,dissent} }`.
  - One line: adversarial-verify is the 2-side ("true vs false") form; rebuttal
    rounds are S8.
- Honor existing Authoring rules in the recipe wording (bound fan-out,
  schema-shaped results, tag hygiene) so the pattern doesn't contradict them.

## 2. `personalities/role-maestro.md` — the menu

Add a short block near the **Spawn Template** (~line 74, right after it): a
"delegate a pattern by name" menu. For each pattern: **name + one-line when to
use**, and a note that the maestro *names* a pattern in the `Cross-cutting
concerns` / scope line of the contract and the lead runs it — the maestro never
drives a Workflow itself. Only `debate` is live; blackboard/tournament listed as
S8.

## 3. Tests

A new `tests/process/test_interaction_patterns.py` (or extend an existing
prompt-assembly test). Assert against the **assembled prompt**, since JD push is
the mechanism:

- Spawning a **lead** yields a prompt containing the `debate` recipe markers
  (e.g. the `## Interaction patterns` heading, "blind to each other", the result
  shape keys). — proves the lead gets the full recipe.
- Spawning a **maestro** yields a prompt containing the menu (the name `debate` +
  its one-liner) but **not** the full skeleton. — proves asymmetric depth.
- Regression guard: `debate` recipe does not introduce a forbidden
  `<hive_actions>`/raw-tag example, and stays consistent with the bounded-fan-out
  Authoring rule.
- (If practical) a check that the `_MAESTRO_DENY` set still contains `Workflow`
  — locks D3 (maestro names, never runs).

Use the existing role-JD loading helper (`load_role_jd`) the way
`test_skill_curation.py` / personality tests already do.

## 4. Docs already landed in the planning PR (no build work)

ADR 0020 and the `CONTEXT.md` glossary entries shipped with `design.md`. The
build PR only touches the two role files + tests.

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` (green)
- Deployed re-smoke (S6/S7 rule — behaviour change in a live role file): spawn a
  lead, confirm via its transcript that the `debate` recipe is in its system
  prompt; ideally drive one real `debate` run end-to-end and eyeball the verdict
  shape.
