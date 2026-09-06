# 016 — Questions

The unknowns going into research/design. Each gets answered (with file
refs) by `research.md` or decided in `design.md`.

## Framing (settled by pre-grill analysis, verify in code)

- **Q0 — Is there really no mechanical dispatch to reroute?** Pre-grill
  finding: leads emit `spawn_worker` only because prompts tell them to
  (`role-lead.md` legacy section, `wake_scheduler.py` kickoff text,
  `scheduler.py` facts prompt); the Workflow path is an in-turn CC tool
  call, not a `hive_actions` verb. If true, 016 = prompt edits +
  enforcement decision + test repointing + the leaf-worktree cleanup
  deferred from 015 — no dispatcher branch gets "repointed".

## Enforcement

- **Q1 — Prompt-only or hard denial?** Trim the JD and trust the model,
  or also cut the lead arm of `can_spawn_worker`
  (`bus/permissions.py:143-144`)? Prompt-only leaves a working backdoor
  (compaction-confused or stale-JD leads drift back); hard denial changes
  audit semantics and flips tests.
- **Q2 — If hard denial: what does the denied lead see?** Today a
  `spawn_worker` denial is log + audit + `continue` — **no message back
  to the actor** (only `message` rejections get the "[action rejected]"
  resend, `message_dispatcher.py:687-715`). A denied lead would believe
  the worker exists and sync-wait forever on a phantom report. Does 016
  extend rejection feedback to the `spawn_worker` branch?

## Drain scope (the 016 ↔ 018 contract)

- **Q3 — Define "drained".** 016's acceptance says "no `spawn_worker` on
  the leaf path"; 018's precondition says "nothing routes through
  `spawn_worker`". Which of these does 016 trim, and which does 018
  inherit live?
  - the maestro arm of `can_spawn_worker` (`permissions.py:140-142`,
    `role-maestro.md` worker docs)
  - the scheduler facts prompt advertising the verb every eval tick
    (`scheduler.py:197`)
  - the kickoff text "Spawn workers if the work warrants subdivision"
    (`wake_scheduler.py:25-30`)
  - the user-facing `/worker spawn` command (`commands/dispatch.py:701`)
- **Q4 — JD edit range.** The legacy material spans roughly
  `role-lead.md:97-143` (legacy section + spawn template + JSON-escaping
  note) — and the lead's **only `kill_entity` documentation lives inside
  it** (~110-112). Maestros can still spawn workers under a lead until
  018, and that lead stays their manager. What survives, what moves,
  what goes?

## Semantics regressions (decide, don't discover)

- **Q5 — Task rows.** There is no `finish_task` verb; worker success is a
  plain `message`, completion is the user's `/task done`. If the Workflow
  path writes no task rows, the dashboard CFD / sankey / failure-scatter
  widgets go progressively blind as leaf work migrates. Write rows from
  the Workflow path, or accept blindness until 017?
- **Q6 — Failure semantics.** `report_failure` → retry → escalate
  (worker→lead→maestro→user, `approval_handler.py:609-690`) has no
  Workflow equivalent — a failed leaf lands only in the lead's synthesis.
  Is in-context judgment + the lead's normal escalation-by-message
  enough, or does the JD need an explicit failure protocol?
- **Q7 — Fan-out caps.** `lead.max_workers` and the autospawn rate limit
  bound only the old path; Workflow width post-016 is mechanically
  uncapped (JD prose only). Same for context pressure — `TaskOutput`
  returns every agent's full results into the lead's context (ADR 0010
  accepted), risking mid-turn compaction on wide fan-outs. Cap in JD
  guidance, or accept?
- **Q8 — Tag hygiene.** A leaf agent's output quoted in the lead's
  synthesis containing a literal `<hive_actions>` block gets the turn
  rejected (known parse-loop failure). Does the Workflow-authoring JD
  guidance forbid leaf agents emitting tags / leads quoting them?
- **Q9 — Visibility window.** 017 trails 016, so between them a Workflow
  run is invisible (no org-tree node, no dashboard row, no Telegram
  ping). Accepted sprint risk — but is there a cheap interim signal
  (e.g. audit event on Workflow start/end) worth adding in 016?

## Inherited homework

- **Q10 — Changed leaf-worktree cleanup.** 015 explicitly deferred the
  commit → PR → remove pipeline for `isolation: 'worktree'` leaf agents
  to 016 (`015/design.md:103-106`), and CC-created sibling worktrees sit
  outside `WorktreeManager`'s bookkeeping. What is 016's design — and is
  it a slice of this ticket or its own follow-up?

## Tests

- **Q11 — Repoint set.** `test_role_jd.py:103` is annotated "removed by
  016". Leaf-dispatch coverage to repoint or keep-until-018:
  `test_process_manager.py:957-1041`, `:1194-1207`,
  `tests/process/test_message_dispatcher.py:422-438`,
  `tests/integration/test_lead_worker_roundtrip.py` (NOT marked
  integration — runs in the normal gate; it covers worker→lead
  hive_actions comms, not just spawning). What replaces the roundtrip's
  comms coverage — a Workflow-leaf roundtrip, or does it survive until
  018? Watch the Ticket-011 coverage floor when deleting tests.
