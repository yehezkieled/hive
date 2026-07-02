# 053 — Tabbed maestro Work view

> S10 web redesign. Opened from the home's project cards (052). Absorbs 049.

## What

A tabbed, conversation-rich workspace for actively working with 2–3 maestros:

- **Tabs** — one per maestro/project you're working with; tapping a home project
  opens/focuses its tab; a "+" opens more.
- **Conversation** — the real thread with that maestro; decisions surface
  **inline**; markdown/tables render.
- **Default target** — the **active tab IS the target** (no `/m:` needed); the
  composer reads "messages go to \<maestro\>".
- **Clear** — a **dedicated button** (not a ⋯ menu); an **anchored popover**:
  *Clear view* (default; keeps the maestro's memory) / *Clear + reset memory*
  (resets the agent's session) / Cancel.
- **History** — a dedicated button; recall / scroll back past conversation.
- A compact **loop-status header** (what the loop's doing) stays visible.

## Why

The Delegator's Desk needs an active-work mode: converse with a few maestros,
switch fast, without re-addressing every message. Folds in the deep-link reply
target (049) and the developer's "erase chat + history + default target" asks.

## Acceptance

- Tabs open/switch across 2–3 maestros; active tab = default target.
- Clear popover offers view-only vs +reset; History recalls past messages.
- A "needs-you" push deep-link opens the right tab, reply-ready.
- `ruff` + `pytest -m "not integration"` green; **deployed re-smoke on a real
  iPad**.

## Non-goals

- The home (052) · steering a live Workflow run (ADR 0014) · per-entity controls
  beyond clear/reset (later).
