# 051 — Questions

What we need to find out before designing the unified needs-you lane.
The grill answers these in `research.md` with file refs, not guesses.

## Current surfaces (what we're collapsing)

1. **Where do the four interrupt surfaces live today?** The ticket says
   2 header bells + 3 SSE bubble renderers — which files/components exactly,
   and what does each render (kind, entity, actions)?
2. **What is the web stack for these components?** (React? vanilla JS + SSE?
   htmx?) Determines what "one lane component" means concretely.
3. **What did 039's awaiting-you attention router already build?** Is there an
   existing rollup/aggregation to extend, or is it per-kind and separate from
   the bells?

## Backing data + APIs (the rollup's inputs)

4. **Per interrupt kind — what API/state backs it?** For each of: decision
   request (029/038), mode elevation, vault action, interactive gate (003),
   blocked/errored loop — the endpoint(s), SSE event name(s), payload shape,
   and where the pending state lives server-side.
5. **How is each item keyed?** Decisions are entity-keyed (ADR 0024,
   one-deep); mode/vault approvals are row-id'd. What key does the unified
   feed use so items dedupe and resolve correctly?
6. **What are the action endpoints?** How do approve/deny and decision-reply
   POST today (paths, payloads)? Are they uniform enough for one action
   dispatcher in the lane, or does the lane switch per kind?
7. **"Errored/blocked" — what state, what action?** Where is an entity's
   errored/blocked state tracked, is it already surfaced to the web, and what
   action (if any) does the lane offer for it — bounce, message, dismiss?

## Delivery mechanics

8. **Rollup transport:** new SSE event, client-side aggregation of existing
   events, or REST endpoint (`GET /api/needs-you`?) + SSE invalidation?
   What does the existing dashboard data flow favor?
9. **Web Push / deep-link coupling (041/048):** the push "actionable set" maps
   to these same kinds, and 048's deep-link highlights a card/chat — does the
   deep-link target anything (bell, bubble) that this ticket deletes? Does it
   need re-pointing at the lane?
10. **Gates on the web post-029:** maestro gates were replaced by the
    conversational decision channel — do interactive gates still reach the web
    UI at all today (e.g. lead gates escalated upward), or is "gate" in this
    ticket's list effectively legacy?

## Contract with 052/053

11. **What interface should the lane expose** so both the Stack home hero
    (052) and the Work view (053) can mount it — mount target, filters
    (per-entity?), item-count for the hero's "calm/loud" state?

## Testing

12. **What tests cover the current surfaces?** Which web/approval test files
    exercise the bells + bubbles + approve/deny paths, and what harness do
    they use — so the collapse keeps the suite green and we know what to
    rewrite vs extend?

## Out-of-scope checks

13. Confirm nothing here needs new approval mechanics or home-layout work
    (that's 052) — the lane is a re-surface of existing capability only.
