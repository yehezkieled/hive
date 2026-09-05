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
| 013 ✱ | [Retire custom advisor for CC native `/advisor`](013-retire-custom-advisor/) | 2026-Q2-S5 | done | #71, #73, #89 |
| 014 | [Trim the VPS skill library](014-trim-vps-skill-library/) | 2026-Q2-S5 | done | #129 |
| 015 | [Lead leaf engine: orchestrate via CC Workflow](015-lead-workflow-leaf-engine/) | 2026-Q2-S5 | done | #74–#79 |
| 016 ✱ | [Migrate leaf dispatch: `spawn_worker` → Workflow](016-migrate-leaf-dispatch-to-workflow/) | 2026-Q2-S5 | done | #109–#111 |
| 017 ✱ | [Bridge Workflow progress to dashboard + Telegram](017-bridge-workflow-progress/) | 2026-Q2-S5 | done | #116–#119 |
| 018 | [Retire the persistent Worker entity](018-retire-worker-entity/) | 2026-Q2-S5 | done | #132–#137 |
| 019 | [Maestro phase-confirmation gate](019-maestro-phase-confirmation/) | 2026-Q2-S6 | done | #179 |
| 020 | [Adapter liveness escalation: auto-bounce jammed PTY sessions](020-adapter-liveness-escalation/) | 2026-Q2-S6 | done | #147 |
| 021 | [Route maestro→user messages first-class](021-router-user-queue/) | 2026-Q2-S6 | done | #171, #175 |
| 022 | [Maestro delegates research (un-bridged interactive gate)](022-maestro-research-delegation/) | 2026-Q2-S6 | superseded by 029+020 | — |
| 023 | [Activate the worktree floor (isolate leaf work) — 015 follow-up](023-activate-worktree-floor/) | 2026-Q2-S5 | done | #92–#95 |
| 024 | [Project ownership & PA write-policy](024-project-ownership-pa-write-policy/) | 2026-Q2-S6 | done | #150–#151, #158 |
| 025 | [Worktree crash-recovery: entities re-adopt their worktrees](025-worktree-crash-recovery/) | 2026-Q2-S6 | done | #146, #153 |
| 026 | [Turn boundary: reader accepts mid-turn during post-tool thinking gaps](026-turn-boundary-acceptance/) | 2026-Q2-S5 | done | #104–#105 |
| 027 | [No-progress timeout false-fires on Workflow runs](027-workflow-run-false-timeout/) | 2026-Q2-S5 | superseded by 017 | #118 |
| 028 | [Scheduler poke corrupts an entity parked at a gate](028-scheduler-poke-gate-guard/) | 2026-Q2-S5 | done | #125 |
| 029 ✱ | [Maestro→user conversational decision channel (was: gate-bridge regression)](029-maestro-gate-bridge-regression/) | 2026-Q2-S6 | done | #144, #157, #160, #164 |
| 030 | [Workflow-turn no-progress timeout false-fires on long runs](030-workflow-turn-no-progress-timeout/) | 2026-Q2-S6 | done | #168, #170 |
| 031 | [Maestro addresses its own lead as `self.<team>`](031-maestro-lead-addressing/) | 2026-Q2-S6 | done | #161, #165 |
| 032 | [Validate entity/team names before they reach git/worktree paths](032-entity-name-validation/) | 2026-Q2-S7 | done | #186 |
| 033 | [PA Maestro self-identity (tell the PA it's the PA)](033-pa-maestro-self-identity/) | 2026-Q2-S7 | done | #191 |
| 034 | [Interaction-pattern library: delivery mechanism + `debate`](034-interaction-pattern-library/) | 2026-Q2-S7 | done | #188 |
| 035 | [Interaction pattern: `blackboard`](035-interaction-pattern-blackboard/) | 2026-Q2-S7 | superseded by ADR 0021 | — |
| 036 | [Interaction pattern: `tournament`](036-interaction-pattern-tournament/) | 2026-Q2-S7 | superseded by ADR 0021 | — |
| 037 | [Responsive / touch shell (iPad daily driver)](037-responsive-touch-shell/) | 2026-Q2-S8 | done | #200 |
| 038 | [Web decision-UI parity (029 → web)](038-web-decision-ui-parity/) | 2026-Q2-S8 | done | #209 |
| 039 | [Awaiting-you fleet view (attention router)](039-awaiting-you-fleet-view/) | 2026-Q2-S8 | done | #202 |
| 040 ✱ | [PWA install (home-screen app)](040-pwa-install/) | 2026-Q2-S8 | done | #205 |
| 041 ✱ | [Web Push (retire Telegram's alert role)](041-web-push/) | 2026-Q2-S9 | done | #231 |
| 042 | [iPad web polish & token-entry UX (037 follow-ups)](042-ipad-web-polish/) | 2026-Q2-S9 | done | #225 |
| 043 | [Standalone PWA: status bar overlaps the top bar (040 follow-up)](043-standalone-status-bar-overlap/) | 2026-Q2-S9 | done | #221 |
| 044 | [Pattern-library awareness (global skills + JD pointer)](044-pattern-library-awareness/) | 2026-Q2-S9 | done | #222 |
| 045 | [CommandRouter registry + read-only Formatter split](045-command-router-registry/) | 2026-Q2-S9 | done | #243 |
| 046 | [Scheduler poke induces malformed `<hive_actions>` block on idle maestros](046-scheduler-poke-prose/) | 2026-Q2-S9 | done | #233 |
| 047 | [Strengthen lead JD pointer: drive pattern-skill *invocation* (044 follow-up)](047-lead-pattern-skill-invocation/) | — | created | — |
| 048 | [Useful Web Push deep-link (041 follow-up)](048-useful-deep-link/) | 2026-Q2-S9 | done | #248 |
| 049 | [Deep-link composer-clear polish (048 follow-up)](049-deep-link-composer-clear/) | — | superseded by 053 | #250 |
| 050 | [Audit & trim the command set (keep useful, cut vestigial)](050-command-audit-trim/) | 2026-Q2-S10 | done | #254 |
| 051 | [Unified "needs-you" lane](051-unified-needs-you-lane/) — designed in Claude design app (impl. via 065) | 2026-Q2-S10 | created | — |
| 052 | [Stack home (Delegator's Desk landing)](052-stack-home/) — designed in Claude design app (impl. via 065) | 2026-Q2-S10 | created | — |
| 053 | [Tabbed maestro Work view](053-tabbed-work-view/) — designed in Claude design app (impl. via 065) | 2026-Q2-S10 | created | — |
| 054 ✱ | [Hive project cleanup (incl. redesign mockups)](054-hive-cleanup/) | 2026-Q2-S10 | created | — |
| 055 | [Project isolation design (sandbox model)](055-project-isolation-design/) | — | created | — |
| 056 | [Per-project worktree floor](056-per-project-worktree-floor/) | — | created | — |
| 057 | [Project isolation: own DB / env / ports](057-project-isolation-db-env/) | — | created | — |
| 058 | [Create-project + maestro flow (git-validated)](058-create-project-flow/) | — | created | — |
| 059 | [Reliability: quota-aware turns](059-quota-aware-turns/) | — | created | — |
| 060 | [Reliability: bounce/idle guard bundle](060-bounce-idle-guards/) | — | created | — |
| 061 | [Codex adapter](061-codex-adapter/) | — | created | — |
| 062 | [OpenCode adapter](062-opencode-adapter/) | — | created | — |
| 063 | [Dogfood kickoff + smoke (finance app on Hive)](063-dogfood-kickoff-smoke/) | — | created | — |
| 064 | [Command surface v2 (behaviour changes to kept commands)](064-command-surface-v2/) | — | created | — |
| 065 | [Implement the Delegator's Desk redesign (from Claude-design mockups)](065-implement-desk-redesign/) | 2026-Q2-S10 | created | — |
| 066 | [Silence plan-quota pings on Telegram (`HIVE_TELEGRAM_QUOTA_ALERTS`)](066-quota-alert-telegram-toggle/) | — | done | — |
