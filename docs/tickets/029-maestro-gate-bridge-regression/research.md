# Research — Ticket 029

Two parts. **Part 1** is the original root-cause of "a maestro `AskUserQuestion`
gate did not reach the user; the turn timed out (zero coordinator activity)" —
it stands, and it's the *motivation* for the redirect. **Part 2** is the
redirect decision (conversational decision channel) + its adversarial red-team.

---

# Part 1 — why the native-gate bridge is untrustworthy

Method: read the gate subsystem first-hand (`pty_session.py`,
`transcript_reader.py`, `gates.py`, `gate_coordinator.py`,
`lifecycle_manager.py`, `tool_policy.py`, `__main__.py`), cross-checked by a
parallel sweep, then verified against a real frozen-gate transcript on disk.

## TL;DR (Part 1)

- **Detection logic is sound.** A gate that follows assistant text IS detected
  correctly — proven by the code path *and* a real frozen `AskUserQuestion`
  transcript on disk. The bug is **not** in `gates.py`.
- **The gate was never detected in Run 1** — the only state consistent with
  *both* symptoms (zero coordinator activity **and** a 180s timeout). A
  *detected* gate parks the turn **forever** (no timeout), so a timeout proves
  detection did not fire.
- **Root cause narrowed to three candidates** (C1/C2/C3) the surviving
  transcripts cannot separate — and **none of them is worth fixing**, because
  the whole mechanism is the wrong shape for a maestro (Part 2).

## Key code facts

- `send()` loops on `await_next_assistant_turn`, which returns `(text,usage)`
  or `Gated`; only `Gated` reaches `_handle_gate → coordinator.resolve()`
  (`pty_session.py:342-348, 390-403`).
- `resolve()` **parks forever** — no timeout, never auto-decides
  (`gate_coordinator.py:119-134`). So a *detected* gate cannot 180s-timeout.
  Run 1 timed out → **the gate was never detected.**
- The "gate row" / any coordinator activity is created only inside `resolve()`
  (`gate_coordinator.py:95-101`), reached only via `Gated`. "Zero coordinator
  activity" ⇔ the reader never returned `Gated`.
- Detection handles gate-after-text: `_tool_use_blocks` scans **all** assistant
  entries and `detect()` returns the first *unresolved* gate tool_use
  (`gates.py:85-93,131-141`). **Verified on disk:** a real frozen capture
  (`…/fix-pty-output/f26472e7…jsonl`) holds `TEXT(...)` then
  `AskUserQuestion <<UNRESOLVED>>`, 0 sentinels — exactly the Run-1 shape — and
  the detector returns `Gate(kind="ask")` over it.

## The maestro-specific ground truth (decisive for the redirect)

`tool_policy.py` (Ticket 015, ADR 0010) already denies **bare native tool
names** to coordinators:

```
_LEAD_DENY    = [Agent, Task, ExitPlanMode, TodoWrite, TaskCreate, …]
_MAESTRO_DENY = _LEAD_DENY + [TaskOutput, TaskStop, Workflow]
```

- **`ExitPlanMode` is already denied to maestros.** So the plan-gate half of
  the 003 bridge is *already dead* for maestros — and it's been denied since
  015 with no plan-gate freezes, which is good evidence **bare-name tool denial
  works in practice**.
- **`AskUserQuestion` is NOT in either denylist** — the only native gate a
  maestro can still emit. That's the single live gate the bridge exists for.

So the bridge survives, for the maestro, to service exactly one tool
(`AskUserQuestion`) — through a fragile detect→translate→inject path — when the
codebase already proves we can simply *deny the tool*. This is the hinge of the
redirect.

## Root cause candidates (Part 1, retained for completeness)

| | Candidate | Why it can't be separated by transcripts |
|--|-----------|------------------------------------------|
| C1 | maestro never emitted a *structured* `AskUserQuestion` | no `AskUserQuestion` in any otter transcript |
| C2 | yolo (`--dangerously-skip-permissions`) suppressed the gate | maestro-only spawn divergence |
| C3 | detector `None` at adapter creation | unlikely; coordinator set before restore |

All three produce identical symptoms (no `Gated` → 180s timeout). The redirect
makes the question moot: maestros stop using the gate entirely.

---

# Part 2 — the redirect: a conversational decision channel

## Why pivot instead of hardening the bridge

A maestro is a **conversational** entity — you address it from Telegram, it
speaks, you reply. That loop (speak → turn ends → you reply → next turn) is its
native grain. A native `AskUserQuestion` is a **TUI menu** that has no
representation on Telegram, so the bridge has to: detect the gate in the
transcript → translate the menu to numbered text → hold the PTY turn open →
inject arrow-keys + Enter back. Every fragile part of 003/029 lives in that
translation. The bridge re-implements a conversation loop *inside one frozen
turn* — when Hive already has a conversation loop.

So: deny the native gates (one line — `ExitPlanMode` already gone, add
`AskUserQuestion`) and have the maestro ask via a `hive_action` that rides the
normal message path. Decisions and the chosen shape: see `design.md`.

## The mechanism already half-exists

`request_decision` is a live hive_action (`bus/actions.py`,
`message_dispatcher.py:369-393`) — but wired lead→own-maestro only:

- `can_request_decision` returns `False` for maestros (`permissions.py:96`).
- It routes to a registered **entity**; `to:"user"` → "Unknown recipient"
  (`message_dispatcher.py:374`) — **exactly Ticket 021's gap**.
- It's fire-and-forget — no waiting state, **no failure-return** (unlike the
  `message` branch, it has no alias resolution and no `_reject_action`).

The redirect closes those three gaps and adds the `awaiting_decision` flag.

## Adversarial red-team (4 agents) — verdict: sound, needs-rework

Holes found and where they're resolved (all in `design.md` / `outline.md`):

| Severity | Hole | Resolution |
|----------|------|------------|
| blocker | `awaiting_decision` field + DB column don't exist; not durable | add field → migration → persist → restore (order matters) |
| blocker | `to:"user"` unroutable (permission + entity-lookup); no failure-return | allow maestro→user; route via 021 sink; add `_reject_action`-style failure path |
| blocker | clearing can't tell a user reply from a peer message | clear only on user-sourced inbound (dispatch-path by name / `from_user` marker) |
| blocker | bare-name denial of `AskUserQuestion` unverified; dismiss-guard doesn't exist | `ExitPlanMode` precedent says it works; confirm on binary; guard optional |
| major | nothing stops the maestro acting in the same turn it asks | truncate remaining actions after `request_decision{to:user}` |
| major | scheduler skip needs its own check (not `is_parked_at_gate`) | add `or entity.awaiting_decision` |
| minor | no nudge cadence → silent forever; restart-reply race; multi-maestro addressing | reuse 3600s nudge; clear-on-restore-if-queued; document PA-default limit |

## Cross-ticket impact (settled)

- **021** is now a **dependency** — the shared `user` router-sink + failure-
  return that both `message` and `request_decision` reuse.
- **031**'s `self.<team>` alias resolver is **shared** by the `request_decision`
  branch.
- **019** is **re-mechanized** onto this channel (its own ticket listed the
  hive_action path as an alternative; we pick it). 019 stays blocked-by-029.

## Non-goals (unchanged)

- Scheduler poke that exploited the window — Ticket 028 (done).
- The no-progress timeout on long Workflow turns — Tickets 027 / 030.
