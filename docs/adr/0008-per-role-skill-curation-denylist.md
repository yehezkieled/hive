# ADR 0008 — Per-role skill curation via a stall-based denylist

- **Status:** Accepted
- **Date:** 2026-06-09
- **Ticket:** [012](../tickets/012-entity-skill-inheritance/)

## Context

Hive spawns each Entity as a native Claude Code session on the user's
global `~/.claude` (no config isolation), so every Entity inherits the
**entire** skill library — user skills, plugin skills, and built-ins.
Some skills are unsafe for an autonomous Entity: they pause mid-Turn
waiting for a human who isn't at the keyboard, deadlocking the org.

Two facts from `research.md` constrain the solution:

1. **Claude Code has no allowlist.** `permissions.deny` /
   `--disallowedTools Skill(name)` *hides* a skill; `allow` only
   pre-approves and does not hide the unlisted ones — and Entities run
   `--dangerously-skip-permissions`, so unlisted skills run regardless. A
   literal "expose only these N" is not achievable without the
   undocumented, partially-broken `CLAUDE_CONFIG_DIR` route.
2. **The Maestro has a human; Leads/Workers do not.** Ticket 003's gate
   bridge routes a Maestro's `AskUserQuestion`/plan gate to Telegram
   (`approver="user"`), where the user answers `/approve gate`. A
   Lead/Worker gate escalates to a parent Entity that cannot answer.

## Decision

Curate skills with a **denylist**, keyed by role, applied through Hive's
existing `entity.disallowed_tools → --disallowedTools` path. A new
`process/skill_curation.py` owns one role→`Skill(...)`-tokens mapping,
merged into the adapter config at spawn.

The blocking criterion is **liveness, not blast radius**: block a skill
iff it *stalls on human input* (a "thinking skill"). Because a Maestro's
stalls reach the human and a Lead/Worker's do not:

- **Thinking skills** (`grill-me`, `brainstorming`, `run-ticket`,
  `plan-next-sprint`, …) → denied for **Lead & Worker**, allowed for the
  **Maestro**.
- **`prototype`** (needs hands-on driving of a built app, un-bridgeable)
  → denied for **all** roles.
- **Everything else** — including fan-out (`build-with-agent-team`,
  `dispatching-parallel-agents`) and side-effecting skills (`git-ship`,
  `update-config`, `telegram:*`) — **allowed for all**. Blocking these is
  theatre: an Entity runs yolo with full `Bash` and can do the same by
  hand.

## Consequences

- **Positive:** reuses proven plumbing (no new flag path); one auditable
  curation list; covers Workers (no `## Tools` markdown needed); the
  Maestro keeps its legitimate "grill the human" capability; the guarantee
  "no human-interactive skill is reachable by a Lead/Worker" is testable
  on the mocked-PTY seam.
- **Negative / accepted:** not a true allowlist — a newly installed skill
  auto-allows until added to the list (**denylist rot**). Mitigated by the
  filesystem-level trim in Ticket 014. Built-in CLI skills are
  un-deniable, but all sit in the allow set, so there is no exposure.
- **Reversal cost:** moderate — the policy lives in one module + three JD
  files; flipping to a stricter model later means adopting the config-dir
  isolation route.

## Alternatives considered

- **True allowlist via `CLAUDE_CONFIG_DIR` + curated `skills/`** —
  rejected: undocumented, partially broken, built-ins persist.
- **Global denylist (role-independent)** — rejected: strips the Maestro's
  human-facing skills, whose gates legitimately reach the user.
- **`--settings permissions.deny` JSON** — rejected: net-new flag path for
  pattern support we don't need (exact names suffice).
- **Blast-radius blocking (fan-out / sensitive)** — rejected: theatre
  given yolo + Bash; adds maintenance for no real containment.
