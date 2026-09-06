# 039 — Questions (unknowns going in)

The open questions before research. `research.md` answers the code-fact ones
with file refs; the grill settles the design forks. Each is tagged **[CODE]**
(answerable by reading the code) or **[DESIGN]** (a judgement call to grill).

## State & semantics

1. **[CODE]** What exactly already encodes "blocked on the user" in
   `view_model.py`? The ticket names three sources — interactive gate, 029
   `awaiting_decision`, and mode/vault approval. Are these one flag or three
   separate ones? Exact attribute names + where set.
2. **[DESIGN]** Is "awaiting-you" **one undifferentiated badge**, or does it
   distinguish *what kind* of answer is wanted (decision vs approval vs gate)?
   Does it carry a **count**, or just presence?
3. **[DESIGN]** "Unread" badge — is there a real read/unread concept, or does
   "unread" just mean "currently awaiting" (appears when blocked, clears when
   answered, no separate dismiss)?

## Hierarchy

4. **[CODE]** How is the Maestro → Lead → Workflow-run hierarchy represented in
   the view model (nesting / lists / ids)? Is there already any "is anything
   under me waiting" rollup, or only per-entity flags?
5. **[DESIGN]** Does a waiting **Lead** bubble its badge **up** to its parent
   Maestro node (so you spot it without expanding), or does the badge sit only
   on the waiting node? (Rollup vs per-node.)
6. **[DESIGN]** Runs are read-only/observed (ADR 0014). A "run-card awaiting
   you" really means *the entity owning the run* is blocked — confirm the badge
   is an **entity** property surfaced on its card, not a run property.

## Filter

7. **[DESIGN]** Does the "waiting-on-me" chip filter the **org tree** (hide
   non-waiting branches) or just a flat **run-card list**? If a non-waiting
   Maestro has a waiting Lead under it, does it stay visible as the *path* to
   the waiting child?
8. **[DESIGN]** Empty state — when nothing is waiting on you and the filter is
   on, what does the board show?

## Transport & clearing

9. **[CODE]** How does the frontend learn current state — SSE push, a
   `refresh.js` poll, or a full snapshot fetch? What's the update latency?
10. **[DESIGN]** The badge must clear when the decision/approval is answered.
    Is clearing acceptable on the existing transport cadence, or does 039 need
    a dedicated event?

## Surface & seams

11. **[CODE]** Which is the **live** surface the cards render on — the Jinja
    templates (`_macros.html` / `_partials`) or the JSX React app
    (`static/dashboard/*.jsx`)? This decides *where* the badge/chip are built.
12. **[CODE/DESIGN]** Relationship to **038** (web decision-UI parity, builds
    `/api/decision` + enriched `decision_request`). The ticket says 039 is
    independent — confirm 039 reads the **existing** `view_model` flag and does
    **not** depend on 038's new payload/store.
13. **[CODE]** Shared-file risk with **037** (touch shell, lands first, edits
    the same `src/hive/web` files) — which exact files overlap, so the 039 PR
    rebases cleanly.
