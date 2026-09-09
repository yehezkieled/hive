---
id: T001
title: Unified needs-you lane
epic: E01
milestone: M1
status: review
priority: P1
depends_on: []
owner: work-t001
auto: no
plan: none
ready: yes
issue: 268
pr: 292
---
## What
Design, as an HTML mockup reviewed in Lavish Editor, the one actionable feed + lane component that folds the four scattered "needs-you" interrupts into one: decision requests (029/038), mode-elevation approvals, vault payment approvals, interactive gates (003), plus blocked/errored loops. Today these live in 2 header bells + 3 separate SSE bubble types in `src/hive/web/templates/landing.html` with copy-pasted approve/deny logic. The deliverable is the approved file `docs/design/T001-needs-you-lane.html`: one `needs_you` feed rendered by one lane component, each item showing entity, kind, prompt/summary, and its inline action (reply field or approve/deny), rendered at both sizes it must serve — the full-width Stack home hero (T002) and the compact Work-view strip (T003) — plus the calm empty state. It starts from the brainstorm draft `docs/archive/tickets/054-hive-cleanup/mockups/needs-you-lane.html`. Drafts live in the gitignored `.lavish/` scratch; the review runs over the Tailscale/MagicDNS link (never `lavish-axi share`). Implementation is T004.

## Why
"Which run needs me" is the scarcest resource (039). Four surfaces for one job is confusing and duplicated. One lane is the supervise-by-exception core of the Delegator's Desk, and it kills the approve/deny code copied across bells and bubbles.

## Acceptance
- [x] `docs/design/T001-needs-you-lane.html` opens in a browser and shows one lane with five item kinds — decision request, mode-elevation approval, vault payment approval, interactive gate, errored/blocked loop — each with entity, kind, summary, and its inline action (reply field or approve/deny).
- [x] The same file shows the calm empty state ("✓ all clear · N loops running").
- [x] The lane is rendered twice in the file, full-width home hero and compact Work-view strip, from the same markup and CSS.
- [x] Hezki reviewed the mockup and approved it as-is (recorded in this ticket's Notes).
- [x] `docs/pm/decisions.md` records the Lavish adoption for T001–T003 and `docs/design/` as the mockups' home.

## Subtasks
- [x] Inventory the four current surfaces (bells + bubbles) in `landing.html` and their actions, and reuse the app's existing CSS tokens
- [x] Draft the lane (five kinds, both sizes, empty state) from the brainstorm mockup under `.lavish/`
- [x] Open it with `npx -y lavish-axi`, hand over the Tailscale link, poll for feedback, iterate
- [x] On approval: export to `docs/design/T001-needs-you-lane.html`, note the approval, add `docs/design/` to nothing else (no static serving)

## Plan
Approach: Start from the brainstorm draft `docs/archive/tickets/054-hive-cleanup/mockups/needs-you-lane.html` and rebuild it in `.lavish/T001-needs-you-lane.html` using the live `src/hive/web/static/landing.css` tokens, so the mockup shows the real product. Add the missing fifth kind (interactive gate: plan / ask / permission), render the same lane markup twice (full-width home hero, compact Work-view strip) and the calm empty state. Review it in Lavish Editor over the Tailscale link, iterate on the annotations, then export the approved file to `docs/design/T001-needs-you-lane.html`.
Touches: `.lavish/T001-needs-you-lane.html` (draft, gitignored), `docs/design/T001-needs-you-lane.html` (approved export), `docs/pm/decisions.md`, `.gitignore`, `CLAUDE.md`, this ticket.
Tests first: no pytest — this ticket ships no Python. The tdd gate is the browser review loop: each acceptance line is checked by opening the file (five kinds present with entity/kind/summary/action, empty state, both sizes from one rule set) and confirmed by Hezki's approval in Lavish.
Decisions to record: the Lavish adoption and `docs/design/` as the mockups' home (already written to `docs/pm/decisions.md`); anything the review settles about the lane's ordering or per-kind actions goes under `## Notes` for T004 to implement.
approved: yes

## Notes
Migrated from `docs/archive/tickets/051-unified-needs-you-lane/`. Grilled 2026-09-08: the Claude design app is replaced by the lavish-axi skill for T001–T003 (see decisions.md), so this ticket is driven by /pm:work like any other; its "tests" gate is the browser review, not pytest. Review only over the Tailscale/MagicDNS link — `lavish-axi share` uploads to a third-party host and is off limits. Non-goals: new approval types, the home layout (T002), push delivery (already 041), any code under `src/hive/web`.

**Approved 2026-09-09.** Hezki reviewed the mockup and approved it as-is. The lavish-axi trial for the design tickets is a success; T002/T003 use it too. Note: this round Hezki reviewed a copy of the file directly rather than through the Lavish browser surface, so no in-browser annotations came back — the verdict was relayed. The Lavish server bind (Tailscale host on port 4387) worked; the direct-review path is a fine fallback.

**Design answers settled at review (for T004 to implement):**
- Lane ordering: **by kind, fixed** — errored → decision → gate → mode → vault. The same kind is always in the same place.
- Work-view compact strip: **this tab's items only, with full inline actions** — only rows for the tab's own maestro, still answerable in place.
- Gate rows render in **three sub-shapes** behind one `gate` badge: plan approval (collapsed plan body + Approve/Reject), AskUserQuestion (the harness's own options as buttons), permission (Allow/Deny).

Approved mockup: `docs/design/T001-needs-you-lane.html` (portable, no Lavish server needed).

**Review gate (2 read-only agents, Sonnet) — findings applied to the approved spec:**
- Confirmed clean: the design tokens match `src/hive/web/static/landing.css` byte-for-byte, and all five action endpoints named in the spec match real routes in `src/hive/web/app.py` (`/api/decision/<entity>/reply`, `/api/gate/<id>/{approve,deny}`, `/api/mode-request/<id>/…`, `/api/vault-action/<id>/…`, `/reset` via `/api/command`).
- Fixed in the mockup: replaced the two Lavish review forms (they called `window.lavish.queuePrompt`, which throws outside Lavish) with a static "settled at review" block; added the missing third gate sub-shape (raw permission, allow-once / allow-always / deny); real `min-width:0` + `overflow-wrap` on the flex rows and long spans; `aria-label` on the reply inputs; `aria-hidden` on decorative glyphs; hero count corrected to 7 rows.

**For T004 (implementation) — must-do, not just visual:**
- **44px minimum touch targets.** The mockup uses compact button/row padding for density on one screen; the shipped iPad UI must give every tap target (row header, approve/deny/reply buttons) at least ~44px, per the portrait-first iPad premise (ADR 0027). This is a build requirement, not a mockup change.
- The Work-view tab bar must wrap or scroll (`overflow-x:auto`) once a maestro has more teams / longer names than the 3-tab mockup shows.

## Proposed changes
