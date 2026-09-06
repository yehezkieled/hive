# 016 — Design

Decisions from the 2026-06-12 grill, keyed to `questions.md`. The
chosen shape: **016 bans Worker creation on every path and removes every
prompt that advertises it; 018 deletes the dead machinery.** Recorded as
[ADR 0013](../../adr/0013-retire-worker-creation-all-paths.md).

## D1 — Full creation ban, with feedback (Q1, Q2, and beyond my rec)

`can_spawn_worker` denies **all** actors — lead, maestro, everyone
(`bus/permissions.py:136-145` arms removed). The user-facing
`/worker spawn` command (`commands/dispatch.py:701`) is removed in the
same ticket. Persistent capacity need = create another lead; the org
model becomes Maestro → Leads → Workflow runs.

Inseparable from the deny: the `spawn_worker` dispatcher branch
(`message_dispatcher.py:471-535`) gains rejection feedback via the
existing `_reject_action` plumbing (`:687-715`) — today denial is
log + audit + `continue` with **no reply**, which would leave a denied
actor sync-waiting forever on a phantom worker. The note names the
replacement ("`spawn_worker` is retired — fan out with the Workflow
tool") and must not invite a retry.

**Alternatives rejected:**
- *Prompt-only* — relies on the model never regressing across
  compactions; the regression is silent (a Worker quietly exists, and
  the dashboard is blind until 017).
- *Lead-only deny (my original rec)* — leaves maestro-spawned Workers
  appearing under leads whose JD no longer documents managing them, and
  makes 018's precondition hope, not proof.

**Trade we signed:** no escape hatch between 016 and 018. If the
Workflow leaf path hits a wall, fallback is a one-commit revert; the
persistent-agent use case is covered by leads themselves.

## D2 — The 016↔018 drain contract (Q3)

> **Drained** = mechanically zero ways to create a Worker. No prompt
> anywhere teaches `spawn_worker`; every code path denies with
> feedback; the only Worker objects left are pre-existing ones living
> out their lives (kill stragglers at deploy — `kill_entity` keeps
> working at the code level until 018).

016 removes *traffic and advertisements*; 018 deletes *machinery*: the
Worker class/lifecycle, the dispatcher branch, the `spawn_worker` verb
parsing (`actions.py:280-298`), `can_spawn_worker` itself, the restore
path for workers, and mechanism-level tests.

## D3 — Prompt surface removal (Q3, Q4)

| Surface | Edit |
|---------|------|
| `personalities/role-lead.md:98-143` | Delete the whole legacy block — spawn_worker docs, spawn template, JSON-escaping note, **and the lead `kill_entity` docs** (~111-113): with creation banned everywhere, a lead has nothing left to kill. |
| `personalities/role-maestro.md:100-101` | Remove the `spawn_worker` verb docs. |
| `process/scheduler.py:197` (and module docstring `:8`) | Maestro facts prompt: drop `spawn_worker`, keep `spawn_team` / `kill_entity` (maestros still kill leads). |
| `process/wake_scheduler.py:25-30` | `_SPAWN_KICKOFF_TEXT`: drop "Spawn workers if the work warrants subdivision" — safe for all spawnee roles. |

## D4 — Workflow-authoring rules in the lead JD (Q6, Q7, Q8)

Three rules replace the old path's mechanical guarantees, added to the
Workflow section 015 created in `role-lead.md`:

1. **Failure enumeration** (replaces `report_failure` retry/escalate):
   a failed or unusable leaf result is retried once with a sharpened
   prompt; still failing → named explicitly in the synthesis to the
   maestro. Never silently drop a failed item.
2. **Bounded fan-out, distilled results** (replaces `max_workers` +
   rate limit): ~10–20 agents per run, bigger jobs as sequential runs;
   agents return schema-shaped summaries, never full dumps — the
   sync-wait returns *everything* into the lead's context, and a wide
   verbose run triggers mid-turn compaction at synthesis time. Also
   spreads draw on the 5-hour plan-quota window.
3. **Tag hygiene** (replaces the per-worker spawn template's
   discipline): leaf prompts must forbid emitting `<hive_actions>` or
   any literal tag; the synthesis paraphrases leaf output, never quotes
   raw tags (the nested-tag parse rejection is established behavior).

No mechanical caps in 016 — nothing to hang one on until it bites.

## D5 — Changed leaf-worktree policy: lead chooses A or C (Q10)

The release-granularity test, written into the JD:

> Would each slice merge alone? → **C**: per-agent
> `isolation:'worktree'`, per-slice PRs (e.g. "fix these 5 unrelated
> bugs"). Is it one deliverable split for speed? → **A** (default):
> agents edit the lead's worktree directly on disjoint files, lead
> tests the combined tree, one commit, one PR (e.g. "type-hint these
> 12 modules").

In A, `isolation:'worktree'` remains the escape hatch for parallel
same-file edits — and then the lead merges the agent branch back and
removes the worktree in the same turn ("you created it, you merge it,
you remove it"). PR granularity matches *release* granularity, not
*parallelism* granularity.

**Rejected: Option B** (Hive-side adoption/GC of CC-created worktrees) —
janitorial code that never answers how changes ship, collides with
Ticket 025, wrong sprint. 025 inherits "adopt/GC orphaned leaf
worktrees" as candidate scope (noted in its ticket); until then,
stragglers are visible via `git worktree list` for a manual sweep.

## D6 — Visibility: accept the blind window (Q5, Q9)

No task rows from the Workflow path, no interim progress bridge — a
running Workflow stays invisible until 017 lands (accepted sprint
risk). A stopgap would be thrown away in two weeks; 017's transcript
watcher is the design. Zero-code fallback if the 016→017 gap stretches:
a one-line JD ping ("starting a Workflow run: <goal>, ~N agents").
The audit stream (`entity.spawn_worker_denied`) doubles as the proof of
drainage for 018.

## D7 — Test strategy (Q11)

- **Behavior tests flip**: lead/maestro autonomous-spawn tests
  (`test_process_manager.py:957-1041`, `:1194-1207` kickoff,
  `test_message_dispatcher.py:422-438`) now assert deny + feedback;
  JD contract tests (`test_role_jd.py:103` — annotated "removed by
  016") assert the new Workflow rules and the *absence* of
  `spawn_worker`.
- **Mechanism tests survive until 018**: `lifecycle_manager.spawn_worker`
  unit tests, `actions.py` parsing, and
  `tests/integration/test_lead_worker_roundtrip.py` (spawns via the
  manager facade directly, below the permission layer — still exercises
  worker→lead comms machinery that 018 deletes).
- New: a denial-feedback test (denied actor receives the rejection
  note) and a `/worker spawn`-removed test. Watch the Ticket-011
  coverage floor when flipping tests.

## Side effects (cross-cutting ✱)

- **ADR 0013** — retire Worker creation on all paths (new).
- **CONTEXT.md** — Worker entry gains a retirement note; new **Leaf
  agent** term (ephemeral Workflow-run agent, not an Entity).
- **`docs/tickets/025-worktree-crash-recovery/ticket.md`** — one line:
  inherits orphaned-leaf-worktree GC as candidate scope.
- **README / DEPLOYMENT** — sweep for `spawn_worker` / worker-creation
  mentions at plan time; declare exact files in `plan.md`.

## Out of scope

- Deleting Worker class/lifecycle/verb/permissions function — 018.
- 017's progress bridge; mid-run steering (S6, ADR 0010).
- Mechanical fan-out caps and quota-aware gating (future hardening).
- Hive-side worktree adoption/GC — 025 candidate scope.
