# Research — Ticket 019: Maestro phase-confirmation gate

Code-grounded answers to `questions.md`. File refs are to the repo at the time
of writing (branch `ticket-019/run-ticket`, off `main` @ 04a91bf). Verified by a
4-way parallel code sweep plus direct reads of the load-bearing files.

## TL;DR — the surprise

**Most of 019 is already wired — as JD guidance, not as an enforced gate.** As of
Ticket 029 (#160, "wire maestro JD to ask the user via request_decision"), the
maestro role JD already tells a fresh maestro to ask the user before it spawns
teams, and the conversational decision channel already parks it while it waits.
What is *missing* is a **code-level guarantee** that the maestro actually asked:
nothing stops a maestro from skipping the ask and emitting `spawn_team`. So 019's
real question is not "build a gate" but **"how strong a guarantee do we want — JD
guidance, or code enforcement?"** (the design fork at the bottom).

## Q1 — What represents a Maestro "phase" today? → **Nothing. No phase concept.**

- The entity has a *runtime* state machine — `EntityState` ∈ {IDLE, STARTING,
  RUNNING, GATED, COMPLETED, ERROR, STOPPED} (`src/hive/models/entity.py:12-44`).
  This tracks **process status**, not **work progress**. There is **no `phase`
  field and no work-phase enum** anywhere (`entity.py`, `models/maestro.py`;
  grep for `phase`/`confirm` returns only the `awaiting_decision` comment at
  `entity.py:221`).
- A maestro is **purely turn-driven**. `register_maestro()` creates it IDLE with
  no subprocess (`process/lifecycle_manager.py:243-283`); the PTY spawns lazily
  on the first `send_to_entity()` (`lifecycle_manager.py:324-368`). Turns are
  triggered externally — scheduler poke (~120 min), a user message, or peer mail
  — and complete atomically (`process/message_dispatcher.py:84-272`). **No state
  transition happens during a normal turn**; nothing today distinguishes "first
  turn / pre-execution" from any later turn.
- **Implication:** "advance past phase 1" maps to no existing event. 019 must
  *define* the boundary. The only natural, already-meaningful boundary in code is
  **"before the first `spawn_team`"** — that is the moment a maestro stops
  planning and starts spending (teams, Workflow fan-out, quota).

## Q2 — How does 029's channel fire and park? → **Fully mapped; 019 reuses it verbatim.**

The conversational decision channel (Ticket 029, ADR 0018) is the mechanism 019
rides. End to end:

```
maestro emits  request_decision{to:"user", text:…}   (actions.py:281-290)
        │
        ▼
message_dispatcher._handle_actions  (message_dispatcher.py:369-446)
  • permission check  can_request_decision()         (line 374)
  • notify user via Telegram (kind=decision_request) (≈line 413)
  • entity.awaiting_decision = True                  (line 418)
  • arm nudge clock + persist to DB                  (≈line 421-422)
  • break → drop trailing actions ("ask-then-act" impossible)
        │
        ▼
persisted: awaiting_decision column                  (bus/entity_store.py upsert; restored on boot, forced IDLE)
        │
        ▼
scheduler.run_once  (process/scheduler.py:236-264)
  • if awaiting_decision: skip the poke, re-ping user ~hourly (_maybe_nudge_decision)
  • maestro is NEVER auto-advanced
        │
        ▼
user replies  →  dispatch._send_to_entity (commands/dispatch.py:636-659)
  • clear_awaiting_decision() FIRST                  (manager.py:196-210, called at dispatch.py:645)
  • then the reply is delivered as the maestro's next turn
```

- **Durability:** the flag survives a Hive restart; a restored maestro stays
  parked and cannot be poked (`entity_store` restore + `scheduler` skip).
- **Clear is user-only:** only the user-reply path clears the flag — peer mail
  and the scheduler never do (`manager.py:196-210` is called solely from
  `dispatch.py:645`). No phantom-clear.
- **Nudge:** `decision_nudge` defaults to ~60 min; a restart re-arms a baseline
  without nudging.

**019 adds nothing here.** It only needs to *cause* a `request_decision{to:user}`
at the phase-1 boundary; parking/nudging/clearing are 029's job.

## Q3 — Where is the Maestro/Lead split enforced? → **`role` string + permission gate; already maestro-only.**

- `Entity.role` is a string ("maestro" | "lead" | "vault"); subclasses hardcode
  it (`models/maestro.py`, `models/team_lead.py`).
- `can_request_decision()` (`bus/permissions.py:89-109`): a **maestro** may
  `request_decision` **only to `user`** (line 106-107); a **lead** may only
  escalate to its parent maestro (line 102-104). A lead **cannot** reach the
  user. → **019's "only maestros gate the user" property is already true by
  construction** — no extra role check needed for the *channel*.
- **PA Maestro** is distinguished by *name*, not role: `is_pa = entity.name ==
  DEFAULT_MAESTRO` (`lifecycle_manager.py:298-322`; `DEFAULT_MAESTRO` =
  `HIVE_DEFAULT_MAESTRO`, default "otter", `config.py:98`). So a per-PA
  difference is cheap to express if design wants one (Q8).
- **Precedents for maestro-only behavior:** skill-curation denylist (ADR 0008,
  thinking skills maestro-only), tool-policy denylist (ADR 0010, Workflow denied
  to maestros), and native-gate denial (`tool_policy.py:29-47` denies
  `AskUserQuestion`/`ExitPlanMode` to maestros — they ask via `request_decision`
  instead). 019 sits naturally in this family.

## The gap 019 actually closes — guidance vs. guarantee

The maestro JD **already** instructs the phase-1 confirmation:

- `role-maestro.md:22-28` (step 4): *"Ask for confirmation with a
  `request_decision` action to `user`, then stop… Do NOT emit a `spawn_team`
  action before the user explicitly approves the plan."*
- `role-maestro.md:77-78` (anti-pattern): *"Skipping user confirmation in step 4.
  …Always propose before spawning."*
- `role-maestro.md:98-119`: full `request_decision` protocol; native prompt tools
  explicitly unavailable.

But this is **model-emitted, not code-enforced**. The `spawn_team` handler
(`message_dispatcher.py:491-540`) is gated **only** by `can_spawn_team(role)`
(line 494) — a pure role check. **There is no check that the maestro has asked.**
A maestro that ignores step 4 and jumps to step 6 will spawn teams and start
spending, and nothing in Hive stops it.

So the design fork is about **how strong the guarantee should be:**

```
                 ┌─────────────────────────────────────────────────────────┐
                 │  019: confirm before a fresh maestro executes (spawns)   │
                 └─────────────────────────────────────────────────────────┘
                                          │
            ┌─────────────────────────────┴─────────────────────────────┐
            ▼                                                             ▼
  A · JD-ONLY (soft)                                     B · CODE-ENFORCED (hard)
  Harden role-maestro.md: make step-4              A + a durable "this maestro has
  confirm mandatory; spell out reject &            round-tripped a decision" flag;
  opt-out wording. Add a test that a               BLOCK spawn_team until it's set
  fresh maestro emits request_decision             (auto-park / reject-and-nudge).
  before spawn_team.                               Guarantees the maestro ASKED.
                                                   (Content-dumb: cannot guarantee
  • Trusts the model to obey its JD.               the user APPROVED — see below.)
  • No new state, no migration.                    • New entity flag + migration.
  • Likely NOT ADR-worthy (reinforces 029).        • New gate in spawn_team path.
  • Tiny; DIRECT lane.                             • ADR-worthy; DIRECT (small) lane.
```

**Content-dumbness caveat (load-bearing for option B).** Per ADR 0018, Hive does
not parse the user's reply — it cannot tell "yes go" from "no stop." So a
code-enforced flag can only mean *"the maestro asked and the user replied,"* not
*"the user approved."* The strongest honest guarantee Hive can give is **"no
`spawn_team` from a maestro that has never completed a `request_decision`→user→reply
round-trip."** Whether the user actually *approved* remains the model's call (it
reads the free text and obeys or re-plans). A naive "confirmed = replied once"
must not be read as "approved."

## Dependencies (from the sweep)

- **031** (`self.<team>` addressing alias) — **done** (#165, merged). Not a
  blocker.
- **021** (first-class maestro→user *messages*) — still `planned`, but **not a
  blocker for 019's core path**: 029's `request_decision{to:user}` → Telegram
  path is already deployed and re-smoked green (#166). 021 broadens the general
  `message{to:user}` sink; 019 rides `request_decision`, which works today.
- **029** (the channel) — **done**, deployed, re-smoked. Fully available.

## What still needs a human decision (→ the grill / `design.md`)

- The **A-vs-B fork** above (guarantee strength) — drives ticket size + whether
  there's an ADR.
- **Q4** creation-only vs every phase boundary.
- **Q5** what *is* the boundary (recommended: "before first `spawn_team`").
- **Q6** reject semantics (what "halt" means; bounded by content-dumbness).
- **Q7** default + opt-out.
- **Q8** does the PA Maestro gate too?
