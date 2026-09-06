# 046 — Scheduler poke induces malformed `<hive_actions>` block on idle maestros

> Surfaced by S9 dogfooding (the iPad daily-driver smoke): idle `otter`
> repeatedly posted a red parse-error wall in chat. Trivial-lane bug fix
> (one prompt reword + a test). Tracks #233.

## What

The scheduler's periodic org-review poke (`PriorityScheduler.build_facts_prompt`)
ended with:

> "Decide: emit `<hive_actions>` with spawn_team / kill_entity, **or respond 'no
> action needed'** if the org is appropriately sized for the workload."

That juxtaposition leads the maestro to put the phrase **inside** a block —
`<hive_actions>no action needed</hive_actions>` — which `parse_actions()` rejects
("Malformed JSON … Expecting value (line 1, col 1). Snippet: 'no action needed'",
or "no closing `</hive_actions>` tag"). The rejection is echoed back, so every
idle poke loops a red error into the chat.

## Why it matters

It pollutes the maestro→user chat with recurring errors and burns a turn each
poke on a no-op the orchestrator throws away. The maestro loop should stay quiet
when there's genuinely nothing to do.

## Fix

Reword the poke (prompt, **not** the parser) so the no-action path is explicit:
when the org is appropriately sized, **do NOT emit a `<hive_actions>` block at
all — reply in plain prose** (e.g. "no action needed"), with a one-line note that
a block whose body isn't valid action JSON is rejected.

Loosening `parse_actions` to swallow malformed blocks is the wrong fix — it would
hide a lead's genuinely-dropped `spawn_team` (the parser's docstring calls this
out: "silent drops let leads believe their spawn worked when it didn't").

## Acceptance

- `build_facts_prompt` steers the idle maestro to plain prose, no block; the old
  "or respond 'no action needed'" juxtaposition is gone.
- Test in `tests/test_scheduler.py`; `ruff` + full `pytest -m "not integration"` green.
- Deployed; idle maestros stop emitting malformed blocks (live re-smoke).

## Non-goals

- Changing `parse_actions` leniency (would mask real dropped actions).
- Any change to the `<hive_actions>` protocol or the chat/decision channels.
