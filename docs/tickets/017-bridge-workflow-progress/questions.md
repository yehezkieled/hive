# Questions — Ticket 017: bridge Workflow progress

The unknowns going in. Each is answered in [`research.md`](research.md)
(empirical / code) or decided in [`design.md`](design.md) (a judgement fork
for the grill). Status tags: ✅ resolved by research · ⚖️ design fork.

## The load-bearing unknown

- **Q1 — Where does a running Workflow's progress physically live, and can
  Hive tap it read-only?** The ticket's own framing: poll `TaskOutput`, parse
  the Lead's transcript, or is there a push channel? ✅ **Resolved** — a real
  run writes a rich on-disk state file (`workflows/wf_<id>.json`) **and** an
  append-only `journal.jsonl`, both under the Lead's own session dir. See
  research.md §1.

## Mechanism

- **Q2 — How does the watcher discover a run and find its files?** ✅ Resolved:
  the Lead's `PtySession` already pins the session `.jsonl`; the workflow
  artifacts sit at a deterministic path derived from it. research.md §2.
- **Q3 — What carries each signal the ticket wants (count / phase / partial
  results / completion / failure)?** ✅ Resolved: all five are in
  `wf_<id>.json`; the journal adds per-agent `started`/`result` deltas.
  research.md §1.
- **Q4 — Does observing progress risk the adapter lock / turn acceptance?**
  ✅ Resolved by ADR 0010 + the reader internals: the bridge is a **separate
  read-only poller**, never in `TranscriptReader`'s accept path, never touches
  `PtySession._lock`. research.md §3, §5.

## Design forks (for the grill)

- **Q5 — Watcher lifecycle & placement.** ⚖️ One poller task per active run
  (spawned on detect, torn down on terminal status) vs. one global sweeper over
  all Lead session dirs. Where does it live (ProcessManager collaborator?).
- **Q6 — Notification granularity: Telegram vs dashboard.** ⚖️ Dashboard wants
  live ticks (count/phase); Telegram must **not** ping per-agent. Which events
  ping the phone (start? completion? failure only?) vs. stream to SSE.
- **Q7 — Org-tree rendering.** ⚖️ Leaf agents are **not** Entities (absent from
  `_entities` and `team.workers`). Render the run as one progress card under the
  Lead node, or as ephemeral per-agent child rows? research.md §4 has the
  current tree shape.
- **Q8 — Failure / cancellation / orphan honesty** (acceptance criterion 3).
  ⚖️ `status` surfaces `failed`/`completed`, but a Lead-process crash orphans a
  run dir at `status:"running"`. What is the staleness/liveness rule, and how
  does it relate to tickets 027 (false-timeout) and 025 (crash recovery)?
- **Q9 — Notification `kind` + payload schema.** ⚖️ New `kind`(s)
  (`workflow_progress` / `workflow_complete` / `workflow_failed`?) and the
  `data` shape; how much partial-result payload to carry before it burdens SSE.
- **Q10 — Coupling to Claude Code's file layout.** ⚖️ The `wf_<id>.json` /
  `journal.jsonl` layout is a CC-runtime detail (like the transcript format
  risk in ADR 0012) and is **Claude-Code-specific** — a future Codex/OpenCode
  adapter (Phase 5) has no such files. Is this an ADR-worthy decision, and how
  does the bridge fail soft if the layout drifts?

## Out of scope (reaffirmed from ticket.md)

- Steering a Workflow from the dashboard/phone (write-back) — S6+.
- The interaction-pattern library (Track 2).
