# 027 — No-progress timeout false-fires on healthy Workflow runs

## What

The transcript reader's no-progress deadline
(`await_next_assistant_turn`, default 180s — `transcript_reader.py:201`)
declares a Lead **timed out** when the Lead is healthy but quiet because
its work is happening inside a **Workflow run**: the leaf agents write to
*their own* transcripts while the Lead's own transcript sits idle,
awaiting the run's return. The reader can't tell "Lead jammed" from "Lead
awaiting its Workflow," so it false-fails a perfectly healthy Lead.
Downstream, the maestro reads that false failure as "the lead died" and
**spawns a duplicate team**.

Also in scope: when the deadline *does* fire, Hive ships the **raw** error
string to the user — `No completed assistant turn in
/home/.../<uuid>.jsonl within 180.0s` — leaking an internal transcript
path instead of a friendly message.

## Why — 2026-06-13 live smoke of Ticket 016

Ticket 016 moved leaf work onto the Workflow primitive, creating a pattern
the timeout was never designed for: the driving Lead goes legitimately
quiet while its leaf agents do the work.

- **Run 1 — duplicate spawn.** Lead `otter.hive-smoke` spawned 02:34:02;
  its auto-kickoff turn hit `No completed assistant turn … within 180.0s`
  at **02:37:08** while its Workflow was running. otter read that as a
  failure and spawned a **second** team `otter.smoke2` at 02:38:06. The
  first lead was left stranded (never produced a turn); the second did the
  actual work. Two leads, two worktrees, one job.
- **Run 2 — no timeout (shows it is latent / timing-dependent).** Lead
  `otter.strutils`'s kickoff was a trivial fast turn ("I have no goal
  yet"), so the long Workflow ran *after* kickoff and the transcript
  advanced often enough to keep resetting the deadline. No timeout, one
  lead, clean — but only by luck of timing.

The deadline is a *no-progress* window that resets on transcript mtime
(`transcript_reader.py:205-209`); a Workflow whose leaf writes never touch
the Lead's transcript starves it.

## Acceptance

- A Lead awaiting an active Workflow run is **not** declared timed-out:
  the reader (or the adapter) treats an in-flight Workflow as progress —
  e.g. via the Workflow task state, or the Lead heart-beating its own
  transcript while it waits.
- A healthy long Workflow run never causes the maestro to spawn a
  duplicate team. Pair with a maestro-side guard: before re-spawning,
  verify the existing lead is actually dead, not merely quiet.
- When a genuine timeout fires, the user sees a friendly message, not the
  raw `No completed assistant turn in <path>` string.
- Tests: a simulated Workflow-running Lead (transcript quiet, run active)
  does not time out; a genuinely stalled Lead still does.
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- The auto-bounce recovery net for genuinely jammed sessions — **Ticket
  020** (complementary: 020 recovers a *real* jam; 027 stops a *false*
  one).
- Interactive-gate waits holding a turn open — **Ticket 029** (gate not
  bridged) / **Ticket 003**.
- Surfacing live Workflow progress to the dashboard/Telegram — **Ticket
  017** (would make the run's activity visible, shrinking the blind wait).

## Notes

Found in the 2026-06-13 live smoke of Ticket 016 — the first real
maestro→lead→Workflow runs. Root cause is the no-progress semantics
(issue #78; Ticket 026's sentinel ladder) meeting 016's new quiet-lead
pattern. One of three Run-1 cluster tickets (027 timeout, 028 poke, 029
bridge); the duplicate-spawn symptom here is downstream of 029's
un-bridged gate plus this false timeout. S6 candidate.
