# 015 — Research (what we found)

Seeded by [`ticket.md`](ticket.md) + [`questions.md`](questions.md). Grounded
in a session-long judgement pass: four parallel code readers over the turn
model, spawn flags, dispatch, and role JDs, plus two live probes against the
pinned binary (`/home/hezki/.local/bin/claude`, **2.1.170**). File:line refs
are to this worktree.

## Three premise corrections (the ticket/sprint were wrong)

The ticket and the S5 sprint risks rest on three assumptions that the code
contradicts. Each changes the design.

### 1. The guard does **not** block the Workflow tool

The ticket's central framing — "relax the lead `Agent`/`Task` guard so a Lead
can author and launch a Workflow" — is false. **`Workflow` is on no denylist.**

- The lead/maestro deny tokens are `Agent Task ExitPlanMode TodoWrite
  TaskCreate TaskUpdate TaskList TaskGet TaskOutput TaskStop`
  (`lifecycle_manager.py:75-76`). No `Workflow`.
- `skill_curation.py` (Ticket 012) denies only `Skill(prototype)` +
  11 thinking skills (`skill_curation.py:16,22-34`) — nothing Workflow-related.
- **Live probe:** a session spawned with the lead's exact deny flags still
  lists `Workflow` among its tools; a plain session does too. The binary has
  it and the guard doesn't touch it.
- No production Entity has ever called it (64 transcripts, zero `Workflow`
  tool_use). It's exposed-but-unused today.

**What the guard actually blocks that 015 needs:** `TaskOutput` and `TaskStop`
— the sync-wait + cancel verbs. So the real change isn't "unblock Workflow";
it's "unblock the two Task verbs the carry-back uses." See design Q2a.

### 2. A Lead does **not** run in a worktree — it runs in the live checkout

The sprint risk says "a Lead runs in a Hive worktree." It does not.

- Only `Worker`s get a cwd; leads fall through to `cwd=None`
  (`lifecycle_manager.py:257-261`).
- `cwd=None` → the PTY inherits the service's `WorkingDirectory` =
  `/home/hezki/projects/hive` (systemd unit), which is **the main checkout the
  deployed `hive.service` imports `src/` from**.

So the hazard is not "nested worktree quirks" — it's that a Lead's leaf agents,
if they write files, write them **into the running service's own source**. This
is what forces the worktree-floor decision (design Q3).

### 3. The guard is conditional and non-persisted (two holes)

- **Hole A — conditional.** The `## Tools` block is written only when a lead is
  spawned with **both** `display_name` and `personality`
  (`lifecycle_manager.py:153-167`). A lead made via `/team create` (neither
  field) gets empty `disallowed_tools` and is **never guarded**.
- **Hole B — non-persisted.** `entity_store` persists no tool lists and
  `ProcessManager.restore` never calls `load_personality`
  (`manager.py:543-559`) — so on a **service restart**, a restored lead
  respawns with no `--disallowedTools` at all. The guard silently evaporates.

Both holes share one root cause: role tool-policy lives in optionally-written,
non-persisted markdown. → move it into code (design Q2c).

## Turn model — the carry-back problem

`PtySession` listens to a Lead's session **only while `send()` is in flight**;
there is no between-turn observer.

- Turn completion = a new `assistant` entry in the session `.jsonl` **+** file
  mtime quiet ≥500ms, polled only inside `await_next_assistant_turn`, called
  only from `send()` (`transcript_reader.py:145-166`, `pty_session.py:300-306`).
  Screen bytes are unused after startup; the byte buffer `_buf` is appended
  forever but read by nobody post-startup (`pty_session.py:140-142,397-414`).
- A Claude Code background task (a Workflow run) **self-re-invokes** the model
  on completion → a turn nobody prompted. Two failure modes:
  - **Between sends:** no `send()` is running → the synthesis (text +
    `hive_actions` + usage) is never parsed, never routed, **lost**.
  - **During a send (the real hazard):** a background entry written after the
    `initial_count` snapshot satisfies the count gate, sits mtime-quiet while
    the model thinks, and `_extract_last_turn` returns **it** as the answer to
    the injected prompt → **mis-attribution**. The reader's own comment ("In
    production this is normally 0 — we await right after sending a prompt")
    documents the assumption a self-invoking task breaks
    (`transcript_reader.py:133-135`).

This is why the design picks **sync-wait (option A)**: the Lead never ends its
turn, so there is no spontaneous turn to lose or mis-attribute. It also forces
two reader hardenings (design Q6).

Adjacent facts the design leans on:
- **Idle-kill** stamps `last_activity_at` once per turn and exempts only
  `GATED` (`message_dispatcher.py:106`, `lifecycle_manager.py:600-615`) — a
  long sync-wait looks idle → would be reaped mid-run. (design Q4)
- **Usage** records only the *final* assistant entry per turn
  (`transcript_reader.py:222-255`) → multi-step turns already undercount;
  Workflow turns more so. Pre-existing, not Workflow-specific. (design Q8)
- The `on_gate_state` callback (`pty_session.py:155/323-326` →
  `approval_handler.py:442`) is an existing per-Entity mid-turn state-broadcast
  channel — a ready template for Ticket 017's progress states.

## Nested worktrees — empirically a non-issue

Live probe: a Workflow agent launched with `isolation:'worktree'` **from a
worktree session** did **not** nest. Git resolved the common git-dir back to
the main repo and created the agent's worktree as a **sibling** under
`/home/hezki/projects/hive/.claude/worktrees/wf_…`, on its **own fresh branch**
(`worktree-wf_…`), clean and writable, **zero** "`main` already used by
worktree" complaints. The historical collision came from two checkouts wanting
`main`; per-agent fresh branches sidestep it entirely. So the worktree concern
is not "will nesting break" — it's "where does a *non-isolated* agent write,"
which is premise 2.

## The denylist's `Task*` block was never reasoned about

`TaskOutput TaskStop TaskList TaskGet` were swept into the deny list in commit
`3586dfa` (2026-05-09) under the "TodoWrite-family" label
(`lifecycle_manager.py:62-64`); the commit's stated problem was *only*
Agent/Task subagenting. **No ADR, comment, or ticket** anywhere considered that
`TaskOutput`/`TaskStop` are also how a CC session monitors and cancels a
background task. So pruning them (design Q2a) reverses an unconsidered
side-effect, not a deliberate decision — and ADR 0010 becomes the first written
record of the whole guard's rationale.

## JD + test impact (inventoried for the build)

**Role JDs** live at repo root `personalities/role-{maestro,lead,worker,vault}.md`,
loaded via `load_role_jd` (`loops.py:8-30`) and injected as the 3rd
`--append-system-prompt` block (`claude_adapter.py:78-79`).

- `role-lead.md` is built entirely around persistent Workers: identity
  (lines 3-9), the spawn workflow (19-36), and the **Agent/Task ban whose
  rationale describes exactly what a Workflow agent is** (46-51). All must be
  reframed (design Q5).
- `role-maestro.md:100-103` also documents `spawn_worker` ("maestro or lead") —
  a second prompt surface, but its removal is 016's, not 015's.
- `LOOP_PROMPTS` reference no spawning — untouched.

**Tests that break in 015:**
- `tests/process/test_lifecycle_manager.py::test_render_auto_personality_locks_tools_for_coordinators`
  — asserts the lead auto-personality contains `disallowedTools: Agent Task`;
  breaks the moment the policy moves to code. **Direct break.**
- `tests/test_role_jd.py::test_lead_jd_documents_spawn_worker` — asserts
  `"spawn_worker" in role-lead.md`; breaks on the JD reframe.
- `tests/test_entity.py::...test_lead_includes_role_jd_with_spawn_worker` —
  asserts a spawn_worker block **and exactly 3** append-prompt blocks → edit the
  JD *in place* (no 4th block) so only the content assertion moves.

(Worker-JD and broad `spawn_worker` behaviour tests — `test_process_manager.py`,
`test_lead_worker_roundtrip.py` — break in **016/018**, not here.)

## Verified environment facts

- Pinned binary `2.1.170` has `Workflow`, `TaskOutput`, `TaskStop`.
- `--dangerously-skip-permissions` (leads inherit maestro `yolo`) makes
  `--allowedTools` a **no-op**; only `--disallowedTools` binds
  (`lifecycle_manager.py:64-68`, commit `3586dfa`). So every new fence must go
  through the deny path.
- A second worktree, `mutable-exploring-sonnet`, sits at the same HEAD — noted
  for collision-awareness; not part of this work.
