# Changelog

One line per shipped sprint, most recent first. Full narrative record
of the legacy Sprints 0–31 lives in
[`docs/archive/PROJECT_PLAN.md`](archive/PROJECT_PLAN.md).

> **Legacy note**: Sprints 0–31 below are the *legacy* meaning of
> "sprint" — single units of shipped work, not 2-week calendar
> windows. The current meaning (2-week window) starts from
> `2026-Q2-S1` in `docs/sprints/`.

## 2026-06

- **2026-06-16** — Sprint 2026-Q2-S6: hardened the live maestro→lead→Workflow→user loop end-to-end. Bridged maestro decisions to the user via a conversational decision channel replacing the interactive gate (029, ADR 0018); auto-bounce jammed PTY sessions (020, ADR 0015); routed maestro→user messages first-class (021); stopped long Workflow turns from false-timing-out (030); fixed a maestro addressing its own lead via a self/me alias (031); enforced project ownership + PA write-policy via a PreToolUse guard hook (024, ADR 0017); made the worktree floor crash-safe with startup reconciliation (025, ADR 0016); and added a code-enforced maestro phase-confirmation gate (019, ADR 0019). 022 superseded by 029+020. Every behavioural ticket re-smoked on deployed code; full `pytest -m "not integration"` + ruff green. Closed early on goal-met (day 4 of 14). Phase 3 Track 2 (interaction-pattern library) + 032/033 carried to S7.
- **2026-06-13** — Sprint 2026-Q2-S5: advanced Phase 3 Workflow-native orchestration — Team Leads now fan out leaf work through the Claude Code **Workflow** tool and the persistent **Worker** entity is deleted. Shipped the lead Workflow engine + worktree floor (015/023), rerouted leaf dispatch `spawn_worker` → Workflow (016), bridged a running Workflow's progress to dashboard + Telegram (017, absorbing 027/#118), and deleted the Worker type — class, lifecycle, `/worker` command, persistence, ~55 code sites + ~40 tests, with a startup `purge_role` guard (018). Plus turn-boundary acceptance (026), the scheduler-poke gate guard (028), native `/advisor` (013), and the VPS skill-library trim (014). 1166 tests green; deployed; **live smoke passed** (deployed maestro→lead→Workflow→3 leaf agents, main clean). Closed early on goal-met. Maestro turn/gate/messaging roughness carried to S6 (029–031).
- **2026-06-09** — Sprint 2026-Q2-S4: closed out Phase 2 Restructure and hardened the live fleet — consolidated the Vault config (Ticket 005), tracked the fire-and-forget async tasks (008), pinned & logged the fleet's Claude Code version (009), repaired the stale lead-worker integration test (010), added a CI coverage floor (011), and curated Claude Code skills per Entity role via a stall-based denylist (012, ADR 0008). Zero behaviour change on refactors; 1118 tests green, coverage 77.49%; deployed to production. Closed early on goal-met — **Phase 2 complete**.
- **2026-06-04** — Sprint 2026-Q2-S3: advanced Phase 2 Restructure — broke up the `process/manager.py` god object into a thin facade + four tested collaborators (Ticket 004, ADR 0006), renamed `WorkerAgent` → `Worker` (006), and removed the headless `claude -p` runtime for PTY-only (Ticket 007, ADR 0007). Zero behaviour change; 1146 tests green; deployed to production. Closed early on goal-met.
- **2026-06-01** — Sprint 2026-Q2-S2: hardened the PTY runtime — the interactive-gate bridge (Ticket 003) detects plan-mode and `AskUserQuestion` gates from the transcript, holds the Turn open, surfaces it on Telegram + web, and injects the user's decision back into the live PTY. Permission gate deferred (ADR 0005). Sprint closed early on goal-met; Phase 1 (Runtime migration) complete.

## 2026-05

- **2026-05-29** — Sprint 2026-Q2-S1 (first 2-week-window sprint): deployed the PTY harness (S30) + QuotaMonitor (S31) to production on the Max plan; adopted the three-altitude doc structure (Tickets 001–002). Plan-mode interactive-gate hang surfaced during deploy testing → tracked as Ticket 003.
- **2026-05-21** — Sprint 31: QuotaMonitor — plan-quota polling + Telegram alerts at 80/90/100% (committed, not yet deployed)
- **2026-05-17** — Sprint 30: PTY harness migration — interactive PTY sessions for plan-billed Claude (committed, not yet deployed)
- **2026-05-09** — Sprint 29: VPS backup strategy — daily `pg_dump` timer (Phase 1 only)
- **2026-05-07** — Sprint 28: Attachment chunking
- **2026-05-07** — Sprint 27: Knowledge as a skill
- **2026-05-07** — Sprint 26: Blueprint chunking
- **2026-05-06** — Sprint 25: Vault build-out
- **2026-05-05** — Sprint 24: Dashboard polish
- **2026-05-04** — Sprint 23: Peer messaging
- **2026-05-02** — Sprint 22: Identity & role JDs (Phases 1, 1.5, 2, 3)
- **2026-05-01** — Sprint 20: Dashboard tab
- _date n/a_ — Sprint 21: Restart persistence (Phase 1 only)

## 2026-04

- **2026-04-30** — Sprint 19: Maestro autonomy
- **2026-04-30** — Sprint 18: File embedding integration
- **2026-04-28** — Sprint 17: File transit
- **2026-04-28** — Sprint 16: Voyage embedding migration
- **2026-04-26** — Post-Sprint 15 polish
- **2026-04-25** — Sprint 15: Web write surface + multi-channel notifications
- **2026-04-25** — Sprint 14: Web landing page (A.2 Paper Ops)
- **2026-04-19** — Sprint 13: Command UX, observability, entity self-review
- **2026-04-18** — Sprint 12: Self-dev readiness bundle
- **2026-04-18** — Sprint 11: Semantic blueprints
- **2026-04-17** — Sprint 10: Auto-management
- **2026-04-17** — Sprint 9: Inter-agent autonomous messaging
- **2026-04-17** — GitHub push + systemd deployment
- **2026-04-16** — Bug fix: 5 critical bugs from code review
- **2026-04-16** — Sprint 8: FastAPI web dashboard with htmx
- **2026-04-16** — Sprint 7: File-based blueprint storage and search
- **2026-04-16** — Sprint 6: Vault entity with security-gated approval flow
- **2026-04-16** — Sprint 4+5: Multi-maestro, permissions, personality, model switching
- **2026-04-16** — Sprint 3b: Modes, loops, priority, swarm, session utilities
- **2026-04-16** — Sprint 3a: Teams, multi-turn sessions, worker lifecycle
- **2026-04-15** — Sprint 2b: Tokens, tasks, audit log
- **2026-04-15** — Sprint 2a: PG port + entity persistence
- _early_ — Sprint 1: MVP — single maestro + single team
- _early_ — Sprint 0: Repo + CI
