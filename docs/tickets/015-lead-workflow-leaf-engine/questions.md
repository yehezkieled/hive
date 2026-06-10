# Questions — Ticket 015

The unknowns going in. A session-long judgement pass (code reading +
empirical probes against the pinned binary) answered the factual ones
before the grill; those answers live in `research.md`. The rest are
design forks the grill must resolve.

## Answered by research (see research.md)

1. **Does the lead guard actually block the Workflow tool?**
   → No. Empirically disproven — the ticket's central premise is wrong.
2. **Where does the guard bind, and is it universal?**
   → CLI `--disallowedTools` flags parsed from auto-generated personality
   markdown only. Two holes: leads spawned without
   `display_name`+`personality` are never guarded, and the guard
   evaporates on service restart (tool lists not persisted).
3. **Does the fleet's pinned CC binary have the Workflow tool?**
   → Yes (2.1.170, verified by probe).
4. **How does a backgrounded Workflow's completion reach the Lead under
   Hive's turn model?**
   → It doesn't. No between-turn observer exists; a mid-turn completion
   triggers a turn mis-attribution race.
5. **Why are `TaskOutput`/`TaskStop`/`TaskList`/`TaskGet` denied to
   leads?**
   → Swept in under a "TodoWrite-family" label (commit `3586dfa`); never
   individually justified, never ADR'd.
6. **Do `isolation: 'worktree'` agents nest cleanly under a worktree?**
   → Yes — empirically they don't nest at all; git creates siblings
   under the main repo, each on a fresh branch. No collisions.
7. **Where does a Lead's session actually run?**
   → `cwd=None` → the service's `WorkingDirectory` = the **main repo
   checkout** the deployed service imports from. (The sprint's "a Lead
   runs in a Hive worktree" is wrong.)
8. **Which JD passages and tests does this touch?**
   → Inventoried in research.md (7 passages of `role-lead.md`,
   `role-maestro.md`, 1 direct + 3 likely test breaks).
9. **Does skill curation (012 / ADR 0008) block anything
   Workflow-related?**
   → No; ADR 0008 explicitly allows fan-out skills.
10. **Has any production entity already invoked Workflow?**
    → No (64 transcripts checked). Exposed but unused.

## Open — for the design grill

11. **Carry-back mechanism** — how does a Lead get the Workflow result
    inside Hive's turn model? Sync-wait inside one turn (blocking
    `TaskOutput`), gate-style park-and-resume (Ticket 003 shape), or a
    persistent per-entity transcript watcher (bleeds into 017)?
12. **Fencing** — with Workflow already exposed, what (if anything) gets
    *added* to the lead denylist, which `Task*` tokens get pruned, and do
    we fix the guard's restart/manual-spawn holes in this ticket?
13. **Worktree / cwd policy** — leaf agents that mutate files must not
    touch the live main checkout. Mandate `isolation: 'worktree'`
    per agent, give Leads a worktree cwd, or both? Who cleans up
    Workflow worktrees (outside `WorktreeManager`'s reach)?
14. **Turn-completion interaction** — does a long Workflow-waiting turn
    trip the 180s reader timeout or the count+quiescence acceptance gate
    (premature accept on an intermediate entry)? Needs an empirical
    answer during implementation.
15. **Idle-kill exemption** — a Lead waiting on a Workflow looks idle to
    `kill_idle_entities` (only `GATED` is exempt). New state, or
    heartbeat?
16. **JD rewrite framing** — the current Agent/Task ban rationale
    describes exactly what Workflow agents are. What new mental model
    does the lead JD teach, and how do both execution paths coexist
    without contradiction during the 015→016 window?
17. **Hermetic acceptance test** — what proves "a Lead can run a
    Workflow fan-out" without a live PTY fleet turn in CI?
18. **ADR scope** — one ADR covering the execution-model reversal
    (ephemeral in-session leaf agents are now correct) + the deliberate
    denylist pruning? The original guard was never ADR'd, so this ADR is
    the first written record of either decision.
19. **Usage accounting** — Workflow-heavy turns undercount tokens
    (only the final assistant entry's usage is recorded). In scope to
    fix, or flagged as a known limitation?
