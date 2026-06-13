# 031 — Maestro addresses its own lead as `self.<team>` → Unknown recipient

> Discovered during: the 016/018 live smoke (2026-06-13). Maestro `hive_dev`
> created team `smoke` (lead `hive_dev.smoke`) then tried to delegate the goal
> to `self.smoke`, which does not resolve — the delegation message died with
> `WARNING: Unknown recipient: self.smoke`. (Worked around by delivering the
> goal directly to the lead.)

## What

Make a maestro reliably address a lead it just spawned. Today the maestro
guesses a recipient like `self.<team>` (using `self` as a stand-in for its own
name) instead of the full dotted `<maestro>.<team>`, and the message is
dropped. Fix so the maestro's delegation lands first time — either by resolving
a `self`/`me` alias to the maestro's own name prefix, or by making the
spawn result / peer directory hand the maestro the lead's exact addressable
name.

## Why

`spawn_team` works and the lead spawns, but the **goal never reaches it** — so
the maestro→lead→leaf chain silently stalls at the first hop. This is the
common case (a maestro delegating to a freshly-created lead), so the failure is
high-frequency, not an edge case. It blocks reliable autonomous orchestration
and made the live smoke require a manual workaround.

## Acceptance

- A maestro that spawns a team can address the new lead and have the message
  deliver on the first attempt — verified on deployed code (no `Unknown
  recipient`).
- Resolution is robust to the model's phrasing: either `self`/`me`.`<team>`
  resolves to `<maestro>.<team>`, or the spawn confirmation + peer directory
  give the maestro the exact name to use (decide in design).
- A truly invalid recipient still rejects with clear feedback (no silent drop,
  no mis-routing to another entity).

## Non-goals

- The maestro→user delivery path (`Unknown recipient: user`) — **Ticket 021**.
- Bridging maestro interactive gates — **Ticket 029**.

## Notes

Surfaces in the `hive_actions` recipient-resolution path (the router /
message_dispatcher recipient lookup) and possibly the maestro role JD's
addressing guidance. Check whether a `parent`/`maestro` alias already exists for
the reverse direction and mirror its resolution for the spawn case.
