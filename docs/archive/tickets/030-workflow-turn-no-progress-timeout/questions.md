# Questions — Ticket 030

The unknowns going in. The ticket's own *Notes* set the frame: **investigate
first why** the no-progress timeout false-fires before patching anything. These
are answered in [`research.md`](research.md).

## Q1 — Did 017's liveness-reset never arm, or does it fire too coarsely?

The ticket assumes the failure lives in 017's `workflow_active` liveness path.
Is that true? Trace the actual reset ladder in
`transcript_reader.await_next_assistant_turn` against a real failing run.

## Q2 — Is this actually Workflow-specific?

The ticket title says "on long Workflow runs." Confirm against the real smoke
transcript: did *only* Workflow turns time out, or did plain turns too? If a
turn with no Workflow in it also false-times-out, the cause is upstream of the
Workflow liveness path entirely.

## Q3 — Where does the reader physically look for the transcript?

The reader polls `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`. Is the
`<cwd-slug>` Hive computes the same directory Claude Code actually writes to?
This is the load-bearing assumption nobody had checked.

## Q4 — If the slug is the cause, what is Claude Code's exact rule?

Hive does `cwd.replace("/", "-").replace(".", "-")`. What does CC really do —
which characters, collapsing of repeats, case-folding? The fix must match CC
exactly or it just moves the bug.

## Q5 — Is there a residual genuine Workflow-liveness gap for a *normal* cwd?

If the slug is the whole cause, then for a clean-named entity 017's machinery
should already keep a long Workflow turn alive and accept it on the sentinel.
Is that so, or is there a second bug hiding behind the first?

## Q6 — Blast radius.

Which entities are affected? Is it one maestro, or does it fan out? Are there
other consumers of the slug? Will the fix turn any currently-green test red?

## Q7 — How do we stop this class of bug from being silent again?

The bug survived because a wrong slug fails *silently* — it looks like a slow
model. What is the cheapest guard that converts a future slug drift into an
obvious signal, without false-firing on Claude Code's lazy dir creation?
