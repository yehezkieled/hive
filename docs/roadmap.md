# Hive Roadmap

Vision and themes over months. Concrete work lives in
`docs/sprints/` (current 2-week window) and `docs/tickets/`
(individual work units). When a phase completes, mark it done and
trim the section.

See [`../CONTEXT.md`](../CONTEXT.md) for terminology and
[`adr/`](adr/) for the decisions behind these phases.

## Phase 1 — Runtime migration  ·  ✅ DONE 2026-06-01

Hive runs plan-billed on a harness-agnostic foundation — the PTY
harness is deployed and live (Tickets 001 + 003). The 2026-06-15
cutoff that moves headless `claude -p` to API billing no longer
threatens Hive; retiring the leftover headless path is Phase 2 cleanup
(Ticket 007).

## Phase 2 — Restructure  ·  ✅ DONE 2026-06-09

Hive is cleanly structured and documented: the `manager.py` god object
is a facade + collaborators (004), the Vault is consolidated (005),
naming drift is resolved (`WorkerAgent` → `Worker`, 006), and the
headless runtime is gone — PTY-only (007). Sprint S4 hardened the live
fleet — tracked async tasks (008), pinned Claude version (009),
repaired integration test (010), CI coverage floor (011), and per-role
skill curation (012). Phase 3 (Workflow-native orchestration) opens next.

## Phase 3 — Workflow-native orchestration  ·  ✅ DONE 2026-06-18

Hive stops hand-rolling its leaf-agent coordination and runs it on Claude
Code's Workflow primitive: Leads fan out deterministic, cheap, reliable
work; persistent Workers retire; a progress bridge keeps every run visible
and (later) steerable from your phone. The engine shipped (015/016/017/018),
and `debate` (034) proved a named coordination shape can be delivered to a
Lead. The broader **named-pattern library** (blackboard/tournament) was
**retired** ([ADR 0021](adr/0021-further-patterns-as-global-skills.md)):
further coordination shapes ship as user-authored **global skills**
(`~/.claude/skills`), not Hive-native recipes — zero engine work, so off the
roadmap.

## Phase 4 — Web dashboard to PWA  ·  ✅ DONE 2026-06-30

Hive is controllable from an iPad, off Telegram: a responsive installable PWA
(S8 — touch shell, decision-UI parity, attention router, PWA install) plus
**Web Push** (S9 — verified on-device; the async ping that demotes Telegram to
debug/log). The web is the daily-driver *surface* now; making it genuinely
*usable* for delegating to autonomous loops is Phase 5.

## Phase 5 — The Delegator's Desk (web for autonomous loops)  ·  IN PROGRESS · S10

Make the web genuinely usable as the surface for **delegating to and supervising
autonomous loops** — a *desk* you run projects from, not a fleet-monitor you read.
"Default calm, exceptions loud": a **Stack home** (needs-you lane as hero +
project glance + delegate bar + quota chip) that opens into a **tabbed Work view**
(2–3 maestro conversations, active tab = default target, clear/history). Promotes
the loop-engineering direction from theme to phase
([ADR 0027](adr/0027-web-delegators-desk.md)); mostly re-surfaces existing
capability and trims the command set first.

## Phase 6 — Dogfood on Hive: build a real product, safely

Prove the loop by building a real product (the finance app) **fully on Hive**, and
let the friction it surfaces drive Hive's hardening. Two hard prerequisites before
any unattended build: **project isolation** (a project's DB / env / ports must not
bleed into Hive's — today the ownership guard fences files, not subprocesses) and
a **per-project worktree floor** (leads build in the project's own repo). Plus
reliability for long unattended runs (quota-aware turns, bounce/idle guards). The
finance-app build is the forcing function; the capabilities it needs are the work.

## Phase 7 — Codex + OpenCode adapters

Vendor independence — pivot the fleet off Claude. Build the `codex` adapter
(ChatGPT/Codex plan) and the `opencode` adapter (provider-agnostic, cheap models
such as GLM); automatic quota-failover once both exist. Runs alongside the dogfood
(Phase 6) — a long unattended build is exactly what needs the quota headroom.

## Phase 8 — Features (ideas)

Each becomes a Ticket when its time comes; until then, just a
one-line bullet here.

- Plan-quota widget on the dashboard.
- Harness view — which Entity runs on which Harness, with each
  plan's remaining quota.
- Quota-aware planning — Maestros treat plan-quota as a shared,
  finite budget; quota becomes a planning input, not a wall it hits.
- The 8 deferred spec features in
  [`archive/AUDIT_2026-05-05.md`](archive/AUDIT_2026-05-05.md) § 7 —
  review and pick any worth doing.
- **Architecture deepening backlog** (from the 2026-06-25 audit; each a
  future backend-weighted Ticket, all following the
  [ADR 0006](adr/0006-god-object-breakup-composition.md) composition
  pattern). The codebase is largely deep already; friction
  concentrates in three fat files:
  - **ActionRouter** — split `message_dispatcher._handle_actions`'s
    844-LOC action switch + per-type `Action.validate()` at parse time
    (audit #1; high blast radius — wants its own sprint).
  - **Turn-coordination collapse** — fold the 4-tier acceptance ladder
    into `TranscriptReader.await_turn()`, thin out `PtySession.send()`
    (audit #2; runtime correctness — needs a deployed PTY re-smoke).
  - **PhaseConfirmationGate** — pull ADR 0019's 3-way-split protocol
    into one owner (audit #5; small/pure, bundles with ActionRouter).
  - **EscalationChain** — unify ApprovalHandler's 4 duplicated
    lead→maestro→user chains (audit #6).
  - **DecisionChannel** — consolidate the scattered `request_decision`
    → user flow (audit #4; the alternative to S9's 045 — pull in first
    if the Web-Push work wants it). Stays entity-keyed (ADR 0024).
  (`Entity` split → `PersonalityLoader`/`CliArgsBuilder` is parked in
  Phase 7 — the Codex adapter forces it. The orphaned
  `runtime/output_parser.py` was removed as a drive-by, 2026-06-25.)

## Priority note

Phase 5 (the Delegator's Desk web) precedes Phase 6 (dogfood): a usable
delegate-and-supervise surface first, then a real build driven from it. Phase 7
(harnesses) runs alongside Phase 6 — a long unattended build is what makes quota
headroom urgent. (The Phase 6/7 work is stubbed as backlog tickets 055–063,
sprint-unassigned — re-grill into a sprint after S10.)

## Direction — loop engineering (emerging)

The long-run aim is **less human in the loop**: Hive's autonomy loop runs
more on its own, reserving the human for the few high-stakes decisions. This
re-weights the surfaces — the **async ping** (Web Push) and a **crisp decision
channel** matter more than the live glance, and coordination shapes (the
interaction-pattern skills) are how the loop **self-organizes its fan-outs
without being told**. Now realized as **Phase 5 (the Delegator's Desk)** — the
web surface for delegating to and supervising those loops.
