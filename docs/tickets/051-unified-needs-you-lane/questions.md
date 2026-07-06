# 051 — Questions

> The grill answers these in `research.md` with file refs, not guesses.

Unknowns to resolve before designing the unified needs-you lane. Each should be
answerable by a code sweep with concrete file references.

## Current surfaces (what we're collapsing)

1. **Where do the 2 header bells live?** Which frontend files/components render
   each, what event or endpoint feeds each, and which of the five needs-you kinds
   (decision / mode / vault / gate / errored) does each bell already cover vs.
   miss?
2. **Where do the 3 SSE bubble renderers live?** Which renderer handles each kind,
   where is the copy-pasted approve/deny logic, and how do the bubbles differ from
   the bells (in-conversation stream vs. header rollup)?
3. **What is the web stack for these components?** React, vanilla JS + SSE, or
   htmx — this fixes what "one lane component" means concretely and what it can be
   mounted into.

## Backing data + APIs (the rollup's inputs)

4. **Per interrupt kind — what state and API backs it?** For each of decision
   request (029/038), mode elevation, vault action, interactive gate (003), and
   blocked/errored loop: the durable store (`awaiting_decision` flag, the row-id'd
   approval rows, gate hold-state, errored signal), the module that owns it, and
   the endpoint(s) + SSE event name(s) + payload shape that expose it.
5. **How is each item keyed?** Decisions are entity-keyed (ADR 0024, one-deep);
   mode/vault approvals are row-id'd. What key does the unified feed use so items
   dedupe, resolve, and get **removed** once answered (event vs. re-fetch)?
6. **What fields fill the acceptance shape (entity, kind, prompt/summary,
   action)?** Which kinds lack a clean `prompt/summary` or a stable id, and where
   would it come from?
7. **What are the action endpoints?** Route paths, request bodies, and auth for
   approve/deny (row-id'd mode/vault) vs. decision-reply (entity-keyed, 038) —
   uniform enough for one action dispatcher, or must the lane switch per kind?
8. **"Errored/blocked" — what state, what action?** Where is an entity's
   errored/blocked-loop state tracked, is it already surfaced to the web, and what
   action (if any) does the lane offer — bounce, message, dismiss, or read-only?

## Delivery mechanics

9. **Does 039's awaiting-you router already roll these up?** Is there an existing
   aggregate view/endpoint to extend, what shape does it return, and which kinds
   does it include or omit today?
10. **Rollup transport:** new REST endpoint (`GET /api/needs-you`?), a derived
    view over existing stores, or client-side aggregation of existing SSE events —
    what does the current dashboard data flow favor, and which `NotificationDispatcher`
    event kinds (fired from `ProcessManager._notify`) drive it?

## Push + deep-link coupling (041/048)

11. **Actionable-set alignment:** the 041 Web Push filter covers
    `decision_request` / `mode_request` / `vault_action_pending` (+ run-ended
    kinds) — does that set match the lane's five kinds exactly, and is any lane
    kind (e.g. gate, errored) absent from push, or vice versa?
12. **Deep-link re-pointing:** 048's push deep-link highlights a card/chat — does
    its target reference a bell or bubble this ticket deletes, what identifier does
    it carry (entity-keyed vs. row-id), and must the tap target be re-pointed at
    the lane?
13. **Gates on the web post-029:** maestro gates were replaced by the
    conversational decision channel — do interactive gates (003) still reach the
    web UI at all today (e.g. lead gates escalated upward), or is "gate" in the
    ticket's list effectively legacy/no-op on the web?

## Contract with 052/053

14. **What interface should the lane expose** so both the Stack home hero (052)
    and the Work view (053) mount it — mount target, per-entity filter, and the
    item-count/empty signal that drives 052's "calm/loud" hero and the "✓ all
    clear" empty state (does 051 or 052 own rendering "nothing needs you")?

## Testing

15. **What tests cover the current surfaces?** Which web/approval test files
    exercise the bells + bubbles + approve/deny + decision-reply paths, what
    harness do they use, and what must be rewritten vs. extended when the 2 bells +
    3 bubbles collapse into one lane?

## Out-of-scope checks

16. Confirm nothing here needs **new approval mechanics** (acceptance: "backed by
    existing APIs, no new mechanics") or home-layout work (that's 052) — in
    particular, does surfacing the errored/blocked kind need new plumbing that
    would push it out of 051's scope?
