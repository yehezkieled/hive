# Questions — Ticket 019: Maestro phase-confirmation gate

The unknowns going into the grill. By the end of `research.md` → `design.md`
every one is answered — with code evidence (file refs) or a recorded decision.

## Resolved upstream (by 029 / ADR 0018) — not re-litigated

- **Mechanism.** The confirmation rides the conversational decision channel
  (`request_decision{to:user}` + `awaiting_decision`), not a native interactive
  gate. 019 *consumes* the channel; it does not build one.
- **Timeout / AFK.** Park forever on `awaiting_decision`; re-ping via the reused
  ~hourly nudge; never auto-decide; the flag is durable across a Hive restart.

## Code-grounded — answer by exploring, not asking

- **Q1. What represents a Maestro "phase" in the code today?** Is there a
  lifecycle/state machine (created → planning → executing → done), or is a
  Maestro purely turn-driven with no phase concept? Where does a freshly-created
  Maestro's *first* turn run, and what would "advance past phase 1" hook into?
- **Q2. How does 029's channel fire and park, exactly?** Where is
  `request_decision{to:user}` parsed, where is `awaiting_decision` set, and where
  does the scheduler skip a parked Maestro? This is the precise seam 019 plugs
  into.
- **Q3. Where is the Maestro/Lead role split enforced** so 019 can be
  Maestro-only — role file, spawn config, or a runtime role check?

## Design decisions — grill the user

- **Q4. Creation-only, or every phase boundary?** The ask names creation; do
  later phase transitions also gate, or just the first one?
- **Q5. What *is* the boundary 019 detects** — a fixed lifecycle transition Hive
  detects, or a boundary the Maestro itself declares (and if so, how)?
- **Q6. Reject semantics.** On "no", does the Maestro die, re-plan, or record
  dissent and continue? What does "halt" mean concretely?
- **Q7. Default + opt-out.** On for all Maestros by default; is a per-Maestro or
  per-session opt-out needed for trusted autonomous runs?
- **Q8. Does the PA Maestro gate too?** It owns no project and is the default
  route for every un-addressed chat — does a freshly created PA Maestro confirm,
  or only project Maestros?
