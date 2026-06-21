# Questions — Ticket 038: Web decision-UI parity (029 → web)

The unknowns going in. `research.md` answers the "how does it work today"
questions with code refs; the design grill (`design.md`) settles the open design
levers. Each question is tagged with where it lands.

## How the 029 decision channel works today (→ research.md)

1. How does a maestro emit a decision to the user, and what does the
   notification carry? **[answered: research.md §A]**
2. How does a Telegram reply unpark the maestro and resume it? Is there an
   ordering that matters? **[answered: research.md §A — clear→send→route, order
   load-bearing]**
3. Is the park state durable across a restart? Is the *question text* stored
   anywhere queryable? **[answered: research.md §A — `awaiting_decision` durable;
   question text NOT persisted anywhere]**
4. Does a plain-text web reply *already* unpark the maestro today (via
   `/api/command`)? **[answered: research.md §A — yes, for the default maestro
   only]**

## The proven mirror pattern (→ research.md)

5. How do `mode_request` / `vault_action` deliver a rich payload, durable store,
   reply endpoint, and pending-on-load recovery? **[answered: research.md §B]**
6. Where does *all* interactive web rendering live — one render path or two
   (landing.html vs the React dashboard)? **[answered: research.md §C — landing.html
   only; the dashboard has no SSE path]**

## Open design levers (→ design grill, design.md)

7. **Storage of the question text.** Today nothing persists it. New durable
   `DecisionStore` table (full mode_request mirror) · overload `mode_requests`
   with `kind='decision'` · or a lighter `last_decision_question` field on the
   entity row? **[design]**
8. **Endpoint keying.** `/api/decision/{entity}/reply` (entity-keyed — matches
   one-pending-per-maestro and the `clear_awaiting_decision(entity_name)`
   signature) vs `/api/decision/{id}/reply` (id-keyed — matches the mode/vault
   convention)? **[design]**
9. **Resume wiring.** Replicate `clear → send → route` inline in `app.py`, or add
   a single `ProcessManager.resume_decision(entity, reply)` that wraps it (cleaner
   mirror, preserves the user-path-only invariant on `clear_awaiting_decision`)?
   **[design]**
10. **Resolution feedback.** No discrete "resolved" SSE event exists for decisions
    (unlike `vault_action_resolved`). Disable the bubble client-side on submit +
    echo the user's reply, and let the maestro's next turn arrive as a normal
    chat line? Or add a `decision_resolved` event? **[design]**
11. **iPad-portrait surface.** The chat rail is `display:none` below 900px
    (`landing.css:1424`), so an inline bubble is invisible on iPad portrait until
    037 lands. Does 038 also surface decisions in a fixed bell-style pending panel
    (visible at narrow widths), rely on 037, or both? **[design]**
12. **Multi-choice (stretch).** The `request_decision` action carries only
    free-text today. Design the payload/store to *accommodate* an optional
    `options` list now (UI deferred), or stay pure free-text? **[design]**

## Lane (→ plan.md)

13. **Direct or fan-out?** The work is producer → store → endpoint → frontend,
    largely sequential (each slice blocks the next), shipping as one coherent
    feature with one re-smoke. Provisional: **direct lane** (one issue, one PR).
    Finalised in `plan.md`. **[plan]**
