# Research — bridging a Lead's Workflow progress

Investigated 2026-06-13. Combines a 6-agent code sweep of Hive with **direct
on-disk inspection of a real Workflow run** executed on this host (run
`wf_3911b298-ffa`, 6 agents). Code claims carry `file:line`; the on-disk
findings are reproduced from the actual run artifacts.

The headline: the agents' first-pass conclusion ("progress is internal to
Claude Code, not tappable") is **wrong**. A Workflow run writes a rich JSON
state file and an append-only event journal to disk, both under the Lead's own
session directory. 017 is a read-only file poller feeding the existing
notification bridge — not a new bridge.

---

## §1 — Where Workflow progress physically lives (THE crux, Q1/Q3)

A Lead runs leaf work by calling the Claude Code **Workflow** tool inside one
long sync-wait Turn (ADR 0010). That run writes, under the parent session's
directory:

```
~/.claude/projects/<cwd-slug>/
  <session-uuid>.jsonl                         ← Lead's transcript (Hive tails this)
  <session-uuid>/                              ← the session's working dir (same stem, no .jsonl)
    workflows/
      scripts/…                                ← persisted workflow script(s)
      wf_<id>.json          ◀── RICH STATE SNAPSHOT, rewritten through the run
    subagents/workflows/wf_<id>/
      journal.jsonl         ◀── APPEND-ONLY event stream (started / result)
      agent-<id>.jsonl                          ← each leaf agent's full transcript
      agent-<id>.meta.json                      ← e.g. {"agentType":"Explore"}
```

### `wf_<id>.json` — the state snapshot (the primary tap)

Top-level keys observed (run `wf_3911b298-ffa`):

```
runId, taskId, workflowName, summary, status, startTime, timestamp, durationMs,
agentCount, phases[ {title} ], workflowProgress[ {type, index, title} ],
result[ … per-agent return values … ], logs[], defaultModel,
totalTokens, totalToolCalls, script, scriptPath
```

This **single file carries every signal the ticket asks for**:

| Ticket signal | Field in `wf_<id>.json` |
|---------------|--------------------------|
| agent **count** | `agentCount` (+ per-agent `state`/`label`) |
| current **phase** | `phases[].title` + `workflowProgress[]` (the phase/agent timeline) |
| **partial results** | `result[]` (the structured/Text returns as they land) |
| **completion** | `status: "completed"`, `durationMs` set |
| **failure** | `status` (honest terminal state — Q8) |
| bonus | `totalTokens`, `totalToolCalls`, `summary`, `workflowName` |

The file's mtime advanced across the whole run (04:10 → 04:16), i.e. it is
**rewritten as state changes** — poll-and-render is the natural read.

### `journal.jsonl` — append-only deltas

Exactly two event types across every journal on disk (177 `started` /
176 `result`):

```json
{"type":"started","key":"v2:<hash>","agentId":"<id>"}
{"type":"result","key":"v2:<hash>","agentId":"<id>","result": { …payload… }}
```

- `count(started)` = agents launched so far; `count(result)` = done so far →
  the live **N/M** without diffing the whole snapshot.
- `result.result` is the agent's **actual return value** → partial results
  stream out one completion at a time.
- **No `phase`/`log` event is persisted to the journal** — confirmed by
  grepping every journal on disk: only `started`/`result`. Phase lives in
  `wf_<id>.json` (`phases` / `workflowProgress`), not the journal.

**Implication for design:** poll `wf_<id>.json` for the rich card
(count/phase/status); optionally tail `journal.jsonl` for clean per-completion
"ping" deltas. The two are complementary, not redundant.

---

## §2 — Discovery is free: the path is deterministic (Q2)

Hive already pins each Entity's session transcript (ADR 0011, session pinning).
`PtySession._claude_projects_dir(cwd)` derives the `~/.claude/projects/<slug>/`
dir (`pty_session.py:62-73`); `_session_path` is the pinned `<session-uuid>.jsonl`
resolved on first send (`pty_session.py:203, 295-302`).

The workflow artifacts sit at `_session_path` **with `.jsonl` stripped**:

```
workflow_state  = <session-dir>/workflows/wf_<id>.json
workflow_journal= <session-dir>/subagents/workflows/wf_<id>/journal.jsonl
   where <session-dir> = _session_path.with_suffix("")   # the sibling dir
```

So the watcher needs no new discovery channel. Two viable triggers:
1. **Watch the dir:** poll `<session-dir>/workflows/` for a new `wf_*.json`.
2. **Read the transcript:** the Lead's `.jsonl` already carries the `Workflow`
   tool_use, the `wf_<id>` string (69 refs in the sample run), and
   `task-notification` entries (154) — Hive tails this file anyway, so the
   `wf_id` can be lifted from there.

Either way the correlation Lead → run is trivial.

---

## §3 — The notification bridge 017 emits into already exists (003 + Sprint-15)

The whole "surface to Telegram + web" half is **ready-made**; 017 just
constructs `Notification`s and dispatches.

```
ProcessManager._notify(message, kind="info", data=None)   manager.py:520
      └─▶ NotificationDispatcher.dispatch(Notification)    dispatcher.py:62
              ├─▶ SSEBroker.send         web/sse.py:48   → dashboard (live, best-effort)
              ├─▶ TelegramBridge.send    telegram/bridge.py:136 → phone ping
              └─▶ EmailDigest.send       notifications/email.py:59 → digest
```

- `Notification` = `(text, kind, data, timestamp)` (`dispatcher.py:14`); `kind`
  already discriminates types (`info` / `mode_request` / `gate` / vault).
- Channels registered at startup: dispatcher created `__main__.py:165`, SSE +
  Telegram + email registered during wiring (`register()` `dispatcher.py:50`).
- **Reuse precedent:** `ApprovalHandler` builds
  `Notification(kind="gate", data={...})` and dispatches — `approval_handler.py`
  (gate waiting / nudge). 017 mirrors this with a `workflow_*` kind.
- **What 017 does NOT reuse:** the durable `mode_requests` approval rows
  (`mode_request_store.py`) — those are for *blocking approvals*. Workflow
  progress is read-only telemetry, so no rows, no doorbell, no
  `GateCoordinator`. This is what makes 017 smaller than 003.

The SSE broker is explicitly best-effort: a slow browser queue drops its oldest
event rather than blocking the dispatcher (`web/sse.py:53-62`) — so a chatty
progress stream can't stall Telegram/email.

---

## §4 — The dashboard org tree, and what Leaf agents break (Q7)

- `/api/org` builds the tree by iterating `team.workers` and resolving each
  name in `process_manager.entities` (`web/app.py:85,101`).
- The landing card counts `workers = sum(len(team.workers) for team in
  entity.teams.values())` and renders `{leads}L · {workers}W`
  (`web/view_model.py:99-102`; macro `_macros.html`).
- **Leaf agents are not Entities** — they're not in `_entities` and not in any
  `team.workers` list (ADR 0010; they have no Hive lifecycle/mailbox per
  CONTEXT.md). So with 016 draining `spawn_worker`, the worker count goes to
  zero and the tree shows a Lead with **nothing under it** during a run — the
  exact "Lead busy → Lead done" blind window 017 must fill.

So the rendering question (Q7) is genuinely new: a run is **not** a set of
Entity rows. Lean candidates — a single transient "active workflow" card under
the Lead (name / phase / N done of M / status), driven by SSE; vs. synthetic
per-agent rows mirroring the old Worker look. The data for either comes from
§1.

---

## §5 — Why this can't perturb turn acceptance (Q4), and the 027 tie-in

- `TranscriptReader.await_next_assistant_turn` accepts a turn on the
  `turn_duration` sentinel (ADR 0012, `transcript_reader.py:213-222`) or, while
  a tool is pending, keeps polling — a Workflow sync-wait is "an unresolved
  tool_use … intermediate, not the Turn's answer" (`transcript_reader.py:242-251`).
- 017's watcher is a **separate poller** over the workflow files. It never calls
  `await_next_assistant_turn`, never holds `PtySession._lock`
  (`claude_adapter.py:171-187` holds the lock for the whole Turn). It can read
  freely while the Lead's Turn is in flight. ADR 0010 mandates exactly this:
  *"a read-only transcript watcher that never touches the lock."*
- **027 relevance (not 017's bug, but adjacent):** the no-progress timeout can
  false-fire on a long run because the Lead's **transcript** goes quiet while
  the **journal** is active (the journal is a different file). 017's poller is
  the one thing that *knows the run is alive* (journal/state mtime advancing).
  Whether to feed that liveness back to harden the reader, or keep 017 strictly
  read-only and leave 027 to S6, is a scope fork (Q8) — flagged, not assumed.

---

## §6 — What 016 already drained (context)

- Worker creation is banned on every path: `can_spawn_worker` returns `False`
  for all actors (`permissions.py:137-143`); the dispatcher branch rejects with
  feedback naming Workflow as the replacement (`message_dispatcher.py:471-501`);
  ADR 0013.
- Every prompt that taught `spawn_worker` was removed; `role-lead.md` now
  teaches Workflow fan-out (block on `TaskOutput`, synthesize, report).
- Net: leaf work runs **only** via Workflow now → the visibility regression is
  live, and 017 is the fix the sprint sequenced after 016.

---

## §7 — Risks / things to verify at build time

1. **Binary-version coupling (Q10).** The file layout above is from this host's
   Claude Code. The deployed fleet pins a binary (ticket 009 / ADR 0010 cites
   2.1.170). **CONFIRM** the deployed Lead binary writes the same
   `wf_<id>.json` + `journal.jsonl` layout before shipping; treat the layout as
   a documented assumption and **fail soft** (missing/changed files → no
   progress card, never crash the Lead path).
2. **Snapshot vs. partial reads.** `wf_<id>.json` is rewritten in place; a poll
   could catch a half-written file. Read-and-tolerate-`JSONDecodeError`
   (skip-this-tick) rather than assume atomicity.
3. **Orphaned runs (Q8).** A Lead-process crash leaves `wf_<id>.json` at
   `status:"running"` forever. The watcher needs a liveness/staleness rule tied
   to the Lead Entity's own state (ties to 025/027).
4. **Token undercount (pre-existing).** ADR 0010 notes Workflow-heavy turns
   undercount tokens (only the final assistant entry is recorded). `wf_<id>.json`
   actually has `totalTokens` — 017 could *optionally* surface real run cost,
   but that's a bonus, not in the acceptance set.
