# ADR 0027 — The web is a Delegator's Desk: delegate + supervise autonomous loops, not a cockpit

- **Status:** Accepted
- **Date:** 2026-07-02
- **Sprint:** [2026-Q2-S10](../sprints/2026-Q2-S10.md) (web redesign)
- **Relates to:** roadmap Phase 5 ("The Delegator's Desk"); the "loop-engineering"
  direction this makes concrete; [ADR 0024](0024-decision-channel-entity-keyed.md)
  (the decision channel the needs-you lane surfaces), Ticket 039 (the awaiting-you
  rollup it consolidates), [ADR 0026](0026-web-push-notification-channel.md) (the
  async ping that pairs with it)

## Context

The web (a fleet-monitor landing + a chat rail) is a good **monitor** and a good
**approver** but a poor **control panel**: a 2026-07-01 feature×exposure inventory
found ~30 of ~35 capabilities are *command-only* (you must type a slash command
in chat), and the developer finds the home page unusable. Separately, Hive's
long-run direction is **loop engineering** — less human in the loop; entities run
autonomous loops and the human is reserved for the few high-stakes decisions.

A first-principles brainstorm ("if I wanted a chatbot that handles my project, how
would I want it?") surfaced three mental models for the home: **(A) a Delegator's
Desk** (hand off goals, supervise by exception), **(B) a Cockpit** (pilot every
agent), **(C) a single Assistant** (converse with one PA that hides the fleet).

## Decision

**Build the web as a Delegator's Desk (A): you delegate goals and supervise by
exception; you do not pilot.** Chosen *because* the future is autonomous loops —
you set them running and are pulled in only when needed, which rules out the
hands-on Cockpit and the turn-by-turn pure-chat model.

Organizing principle: **"default calm, exceptions loud."** When every loop runs
fine the page is quiet and glanceable; when something needs you it is unmissable.

The desk does five jobs — **delegate** (give a project a goal in plain language),
**glance** (each project's loop status at a glance), **handle exceptions** (the
needs-you hero), **trust & verify** (a readable activity trail of what a loop
did), **manage projects** — with the **needs-you lane as the hero**.

Two surfaces:

1. **Stack home** (Ticket 052) — needs-you lane (hero) → project-glance cards →
   an always-present delegate bar, plus an ambient quota chip. Portrait-first.
2. **Tabbed Work view** (Ticket 053) — tapping a project opens a tab; keep 2–3
   maestros open, each a real conversation. The **active tab is the default
   target** (no `/m:`); a dedicated **Clear** (anchored popover: clear-view vs
   +reset-memory) and **History** recall.

The **needs-you lane** (Ticket 051) consolidates the four scattered interrupts —
decision (029), mode, vault, gate — plus errored loops into one feed, replacing
the two header bells + three bubble renderers.

## Alternatives rejected

- **B · Cockpit** (pilot every agent, controls on each). Too hands-on for a
  loop-driven world; it's what today's page approximates and the developer finds
  unusable. Rejected — supervision, not piloting.
- **C · single Assistant** (one PA, fleet hidden, pure chat). Most "chatbot"-like,
  but loops aren't turn-by-turn conversation and it hides the project/loop status
  a delegator needs. Rejected as the *home*, though its chat-front-door lives on
  in the tabbed Work view.
- **Keep the fleet-monitor + promote commands piecemeal.** Leaves the wrong
  mental model (monitor, not desk) and the command-only friction. Rejected in
  favour of a from-first-principles home.

## Consequences

- The redesign is mostly **re-surfacing existing capability**, not new plumbing:
  the needs-you data (039 + the approval/decision APIs), project status (024 +
  017), delegate (`/api/command`), quota (`/quota`) all exist. The **one new
  binding** is project ↔ home/loop status — which the S11 dogfood needs anyway.
- The web stops being a monitor and becomes the **cockpit for driving autonomous
  loops** — the surface the loop-engineering direction requires.
- S10 builds it (051 lane · 052 home · 053 work view · 050 command trim first ·
  054 cleanup last). The dogfood + isolation + harnesses that *fill* it are S11.
- **Number-race caveat:** ADR `0027` is next-free against origin/main at authoring
  time; re-verify and renumber at ship if a parallel worktree took it.
- Reversible in principle, but a home rebuilt on the wrong mental model is
  expensive to unwind — hence deciding the model (A) before any layout.
