# Questions — Ticket 028

The unknowns going in. `research.md` answers each with file refs.

## Mechanism

1. Exactly which call path turns a scheduler poke into keystrokes on a
   parked TUI menu? (scheduler → ? → PTY stdin)
2. Is the menu-submission a property of *every* sender that types into the
   PTY, or something specific to the scheduler? (decides A vs B)
3. Which gate kinds park the PTY on a menu — only AskUserQuestion, or also
   plan-approval and permission prompts? (decides whether banning
   AskUserQuestion could ever be sufficient)

## Detection signal

4. What is the authoritative in-memory signal for "this entity is parked at
   a gate right now," and when is it set/torn down?
5. Is that signal reliably set in the *actual* failure scenario — an
   un-bridged maestro gate (Ticket 029)? Or does 029's gap mean the
   coordinator isn't tracking it (so we'd need a fallback)?
6. Is the session-state `waitingFor` field needed as a fallback, or does the
   in-memory signal already cover every parked gate?

## Safety of the guard

7. Does guarding `send_to_entity` risk blocking a *legitimate* gate answer?
   (i.e. does gate resolution flow through `send_to_entity`?)
8. If we skip a send to a parked entity, do we lose messages — peer
   messages, the user's text, the facts poke?
9. Is there an existing code pattern for the pending-gate check we should
   match rather than invent?

## Build surface

10. Where does the guard physically go in `send_to_entity`, relative to the
    entity lookup, the `last_activity` update, and the inbox drain?
11. What is the test scaffolding for the scheduler and the dispatcher, and
    does it already expose a `gate_coordinator` we can drive?
