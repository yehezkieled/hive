# Roadmap

Milestones are ordered versions, not calendar targets. Work moves top to bottom.
Ideas with no home yet go in Backlog.

Four milestones shipped earlier — runtime migration,
restructure, Workflow-native orchestration, and the web dashboard to PWA. The
archived roadmap calls them "Phases 1–4"; their history is in
`docs/archive/roadmap-phases.md`, `docs/archive/sprints/`, and
`docs/archive/tickets/`. Decisions behind them are in `docs/adr/`.

## M1: Delegator's Desk
Goal: The web is genuinely usable for delegating to and supervising autonomous loops — a Stack home (needs-you lane as hero + project glance + delegate bar + quota chip) that opens into a Project page and live worker view, served by a loopback gateway over Tailscale and reading the first mate's backlog (ADR 0027, ADR 0030).

## M2: Act and live
Goal: The website acts, not only reads — decisions lane, chat with the first mate, control verbs, merge step-up — then goes live (SSE, Web Push, live tail) on its final origin, running in production on the PC. (ADR 0030)

## M3: Ticket sync and project second mates
Goal: Tickets are editable from the terminal first mate, the website and a project's second mate, with one owning home per ticket; Hive is promoted to its own second mate and the finance app is the next candidate. Parked Hive tickets are landed or closed first. (ADR 0030)

## M4: Cut-over
Goal: Telegram becomes an optional ping and Hive's own Entity runtime (Maestro, Lead, adapters, entity tables) is retired. Codex/OpenCode adapters are not built; firstmate dispatches those harnesses. (ADR 0030)

## Backlog
- Plan-quota chip fed by `quota-axi`.
- Harness view — which Entity runs on which Harness, with each plan's remaining quota.
- Quota-aware planning — Maestros treat plan quota as a shared, finite budget, a planning input rather than a wall.
- The 8 deferred spec features in `docs/archive/AUDIT_2026-05-05.md` § 7 — review and pick any worth doing.
- Architecture deepening (2026-06-25 audit, each a backend ticket following ADR 0006): ActionRouter split of `message_dispatcher._handle_actions`; turn-coordination collapse into `TranscriptReader.await_turn()`; PhaseConfirmationGate as one owner; EscalationChain unifying ApprovalHandler's four chains; DecisionChannel consolidating the `request_decision` → user flow.
- Mid-run Workflow steering (ADR 0014) and new observability widgets.
