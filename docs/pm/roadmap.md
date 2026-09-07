# Roadmap

Milestones are ordered versions, not calendar targets. Work moves top to bottom.
Each milestone lists its epics. Ideas with no home yet go in Backlog.

Four milestones shipped before this layout existed — runtime migration,
restructure, Workflow-native orchestration, and the web dashboard to PWA. The
archived roadmap calls them "Phases 1–4"; their history is in
`docs/archive/roadmap-phases.md`, `docs/archive/sprints/`, and
`docs/archive/tickets/`. Decisions behind them are in `docs/adr/`.

## M1: Delegator's Desk
Goal: The web is genuinely usable for delegating to and supervising autonomous loops — a Stack home (needs-you lane as hero + project glance + delegate bar + quota chip) that opens into a tabbed Work view, on a trimmed command set. "Default calm, exceptions loud." (ADR 0027)
Epics: E01, E02, E03

## M2: Dogfood on Hive
Goal: The finance app builds fully on Hive as a real product, with the project isolated from Hive's own files, DB, env, and ports, and long unattended runs surviving quota walls and idle guards.
Epics: E04, E05, E06

## M3: Harness adapters
Goal: Entities run on Codex and OpenCode as well as Claude Code, through the Adapter interface, with automatic quota failover between harnesses.
Epics: E07

## Backlog
- Plan-quota widget on the dashboard.
- Harness view — which Entity runs on which Harness, with each plan's remaining quota.
- Quota-aware planning — Maestros treat plan quota as a shared, finite budget, a planning input rather than a wall.
- The 8 deferred spec features in `docs/archive/AUDIT_2026-05-05.md` § 7 — review and pick any worth doing.
- Architecture deepening (2026-06-25 audit, each a backend ticket following ADR 0006): ActionRouter split of `message_dispatcher._handle_actions`; turn-coordination collapse into `TranscriptReader.await_turn()`; PhaseConfirmationGate as one owner; EscalationChain unifying ApprovalHandler's four chains; DecisionChannel consolidating the `request_decision` → user flow.
- `Entity` split into `PersonalityLoader` / `CliArgsBuilder` — forced by the Codex adapter (M3).
- Mid-run Workflow steering (ADR 0014) and new observability widgets.
