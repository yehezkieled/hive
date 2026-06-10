# 017 — Bridge Workflow progress to the dashboard + Telegram

## What

Surface a running Workflow's state — agent count, current phase, partial
results, completion — into Hive's notification stream so it shows on the web
dashboard and pings Telegram. Reuse the **Ticket 003** interactive-gate
bridge pattern (the SSE broker + notifications + observe seam).

## Why

When leaf work moves from persistent Workers to ephemeral Workflow agents
(016), the org tree loses per-leaf visibility — you'd no longer see what's
running from your phone, which is Hive's core value. This bridge restores
that visibility for the new model: a Lead's Workflow appears as live
progress, not a black box.

## Acceptance

- A running Workflow launched by a Lead emits progress events (count / phase /
  completion) into the SSE + notification stream.
- The dashboard renders an in-flight Workflow's progress; a completion pings
  Telegram.
- Failure / cancellation of a Workflow surfaces honestly (not a silent hang).
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- **Steering** the Workflow from the dashboard / phone (write-back control) —
  S6+. This is read-only visibility.
- The full interaction-pattern library (Track 2).

## Open design questions (→ research/design)

- How does the parent (Lead) obtain live progress — poll `TaskOutput` / the
  run output, or does the Workflow runtime expose a push channel Hive can tap?
- Mapping Workflow phases / agents onto the existing dashboard org-tree
  widgets.

## Cross-cutting ✱

Touches `src/hive/web/*` (dashboard) and the notifications module. Declare in
`plan.md`.

## Blocked by

- 015 (need Leads running Workflows to have progress to bridge).
