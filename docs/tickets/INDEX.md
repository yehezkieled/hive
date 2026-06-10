# Tickets

Registry of all Tickets. Update the row whenever a Ticket changes
state (created → in progress → done). Cross-cutting Tickets are
marked with ✱.

| ID | Title | Sprint | Status | Issues |
|----|-------|--------|--------|--------|
| 001 | [Deploy PTY runtime + QuotaMonitor](001-deploy-pty-runtime/) | 2026-Q2-S1 | done | — |
| 002 ✱ | [Restructure project management docs](002-doc-restructure/) | 2026-Q2-S1 | done | — |
| 003 | [Interactive-gate bridge for the PTY harness](003-plan-interaction-bridge/) | 2026-Q2-S2 | done | #22–#27 (#26 deferred) |
| 004 | [Break up `process/manager.py`](004-manager-py-breakup/) | 2026-Q2-S3 | done | #41–#45 |
| 005 | [Consolidate the Vault](005-vault-consolidation/) | 2026-Q2-S4 | done | — |
| 006 | [Rename `WorkerAgent` → `Worker`](006-worker-rename/) | 2026-Q2-S3 | done | — |
| 007 ✱ | [Remove the headless (non-PTY) runtime path](007-remove-headless-runtime/) | 2026-Q2-S3 | done | #52 |
| 008 | [Track untracked fire-and-forget tasks](008-track-background-tasks/) | 2026-Q2-S4 | done | — |
| 009 ✱ | [Pin & align the fleet's Claude Code version](009-pin-claude-version/) | 2026-Q2-S4 | done | — |
| 010 | [Repair the stale lead-worker integration test](010-repair-integration-test/) | 2026-Q2-S4 | done | #58 |
| 011 | [Add a CI coverage floor](011-ci-coverage-floor/) | 2026-Q2-S4 | done | #61–#62 |
| 012 | [Curate & expose CC skills to Entities](012-entity-skill-inheritance/) | 2026-Q2-S4 | done | #67 |
| 013 ✱ | [Retire custom advisor for CC native `/advisor`](013-retire-custom-advisor/) | 2026-Q2-S5 | in progress | #71 |
| 014 | [Trim the VPS skill library](014-trim-vps-skill-library/) | 2026-Q2-S5 | planned | — |
| 015 | [Lead leaf engine: orchestrate via CC Workflow](015-lead-workflow-leaf-engine/) | 2026-Q2-S5 | done | #74–#79 |
| 016 ✱ | [Migrate leaf dispatch: `spawn_worker` → Workflow](016-migrate-leaf-dispatch-to-workflow/) | 2026-Q2-S5 | planned | — |
| 017 ✱ | [Bridge Workflow progress to dashboard + Telegram](017-bridge-workflow-progress/) | 2026-Q2-S5 | planned | — |
| 018 | [Retire the persistent Worker entity](018-retire-worker-entity/) | 2026-Q2-S5 | planned | — |
| 019 | [Maestro phase-confirmation gate](019-maestro-phase-confirmation/) | S6 (cand.) | planned | — |
| 020 | [Adapter liveness escalation: auto-bounce jammed PTY sessions](020-adapter-liveness-escalation/) | S6 (cand.) | planned | — |
| 021 | [Route maestro→user messages first-class](021-router-user-queue/) | S6 (cand.) | planned | — |
