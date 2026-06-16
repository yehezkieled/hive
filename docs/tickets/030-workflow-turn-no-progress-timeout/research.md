# Research — Ticket 030

Investigated 2026-06-14, grounded in **direct on-disk inspection of the real
016/018 smoke run** on this host plus a 5-agent adversarial verification pass
(each agent tried to *refute* one claim). Code claims carry `file:line`;
findings are reproduced from the actual run artifacts.

> **Headline: the ticket's premise is wrong.** 030 was filed as "017's
> liveness-reset isn't holding on long Workflow runs." It is **not** a Workflow
> problem and **not** a liveness-reset problem. The reader was pointed at a
> **transcript directory that does not exist**, because Hive computes Claude
> Code's project-dir slug differently than Claude Code does. Every turn —
> Workflow or not — false-timed-out at exactly `prompt + 180s`. 017's machinery
> is sound; it was reading the wrong directory too.

---

## §1 — What the smoke actually did (the decisive evidence)

The smoke lead `hive_dev.smoke` ran three turns. The **real transcript**
(`…-hive-dev-smoke/87aa3beb-….jsonl`, 84 875 bytes) shows **all three completed
successfully**, each with a `turn_duration` sentinel:

```
prompt 14:26:25.965 ─▶ sentinel 14:26:41.893   turn 1 — KICKOFF, no Workflow at all
prompt 14:29:26.517 ─▶ sentinel 14:30:00.886   turn 2 — Workflow, 3 agents, ok
prompt 14:32:27.017 ─▶ sentinel 14:32:48.700   turn 3 — Workflow, 3 agents, ok
```

Yet `journalctl` shows each `send()` declared timed-out at **exactly
`prompt + 180s`**:

```
auto-kickoff  … failed: Turn did not complete within 180.0s   @ 14:29:25.99  (= 14:26:25.965 + 180.02s)
wake-on-inbound … failed: Turn did not complete within 180.0s @ 14:32:26.49  (= 14:29:26.517 + 180s)
```

**Turn 1 had no Workflow in it.** (A1/Q2) So the failure is upstream of the
Workflow liveness path entirely — it is not Workflow-specific.

## §2 — Root cause: the transcript-dir slug mismatch (Q3/Q4)

Claude Code stores a session's transcript under `~/.claude/projects/<slug>/`
where the slug is the cwd with characters rewritten. Hive's
`_claude_projects_dir` (`pty_session.py:73`) does:

```python
slug = str(cwd).replace("/", "-").replace(".", "-")   # only '/' and '.'
```

Claude Code's **real** rule (verified, §4) rewrites **every non-alphanumeric
char** to `-`. The smoke lead's cwd was `…/worktrees/hive_dev.smoke` (the
maestro is named `hive_dev`, with an underscore):

```
            …/worktrees/hive_dev.smoke
Claude Code →  …-worktrees-hive-dev-smoke   ← real transcript lives here   ✓ exists
Hive looks  →  …-worktrees-hive_dev-smoke   ← reader polls here            ✗ NEVER created
```

`ls ~/.claude/projects/*hive_dev*` → nothing. The underscore directory Hive
searched for has never existed.

## §3 — Why one wrong directory kills *both* halves (Q1)

`_claude_projects_dir` is the **headwater** of the whole transcript pipeline.
Session pinning (ADR 0011) resolves `_session_path = _project_dir /
f"{session_id}.jsonl"` (`transcript_reader.py:97`) — so even a perfectly
**correct** session-id pin lands in the **wrong directory**. And
`session_dir = _session_path.with_suffix("")` (`pty_session.py:293`) feeds
`run_active()` / `parse_run_dir()` — so the 017 liveness predicate reads the
wrong directory too.

```
 send(prompt) ─▶ _session_path = <WRONG dir>/<uuid>.jsonl     (file never appears)
                     │
  real turn ░▓▓░▓ sentinel ─▶ <RIGHT dir>/<uuid>.jsonl  ── reader never looks here
                     │
  reader polls WRONG path:  _stat_mtime → None,  _count_assistant_entries → 0
       ├─ mtime-change reset   needs mtime ≠ None        → never fires
       ├─ sentinel acceptance  needs count > initial(0)  → never fires
       ├─ pending-tool reset   gated on count > initial  → block never entered
       └─ workflow_active(180) session_dir also WRONG    → run_active → False
                     │
                     ▼  at prompt + 180s
            TimeoutError "Turn did not complete within 180.0s"
```

A single wrong directory starves **every** reset path *and* the liveness
backup. That is why it looked like "017 isn't holding" — 017 was blindfolded by
the same bug.

**Live repro (adversarial agent):** running the real
`await_next_assistant_turn` against the actual nonexistent path with
`timeout=1.0` raised `TimeoutError` at +1.02s and `workflow_active(180)` was
`False` — no reset, no accept, exactly as traced.

## §4 — Claude Code's exact slug rule (Q4)

Confirmed empirically (stronger than disassembly): for **all 76 genuine CC
project dirs** on this host (those holding a real `<uuid>.jsonl`), the source
cwd was recovered from the `cwd` field inside each transcript and the candidate
regex applied. **72 had a recoverable cwd; 0 mismatches.** Three sub-checks:

| Property | Test | Result |
|----------|------|--------|
| char class | `re.sub(r"[^a-zA-Z0-9]", "-", cwd)` reproduces every dir | ✓ exact |
| **no collapsing** | `/.claude` → `--claude` (double dash) in real dirs | ✓ not collapsed |
| **no case-fold** | 10 dirs preserve uppercase (`…2VoW2udMRK…`) | ✓ case kept |

The lone underscore-bearing dir
(`-tmp-pytest-…-test_start_adds_continue_when_0`) is a **Hive test fixture**
(only a 21-byte `session.jsonl`, never a `<uuid>.jsonl`) created by Hive's own
buggy code via the helper at `test_pty_session.py:186` — it confirms, rather
than refutes, that real CC always uses dashes.

**The fix:** `slug = re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))`. (`re` already
imported, `pty_session.py:9`.)

> Caveat (agent): exotic chars a cwd could *theoretically* hold (space, unicode,
> `@`, `+`) weren't present in the corpus to test directly, but the regex maps
> all of them to `-`, consistent with CC's rule. The on-disk corpus is the
> ground truth; the disassembled `/[^a-zA-Z0-9]/g` is a matching hypothesis.

## §5 — No residual Workflow-liveness gap for a normal cwd (Q5)

For a **clean-named** entity the slug is already correct, so the reader sees the
real transcript. Traced against the real smoke transcript at three truncation
points:

- While the run is in flight, the last assistant entry is the **`TaskOutput`**
  sync-wait (ADR 0010) with an unresolved `tool_use` → `_has_pending_tool_use`
  is `True` → the deadline resets every poll, for the whole run however long.
- The only window where pending-tool flips to `False` (tool_result → next
  assistant) is **bracketed by transcript writes** that bump mtime and reset the
  deadline; in the smoke it was ~6–8s. A >180s pure-think stall there is the
  **genuine** stall acceptance #4 *wants* to time out — not a false-fire.
- At the sentinel, `count > initial` **and** `sentinels > initial` → accepted.

So once the slug is fixed, 030's acceptance is met by the **existing** 017
machinery, with **no change to the liveness path**. (Verdict: confirmed, 0.85.)

> **The 0.85 caveat is load-bearing:** this is reasoned from a short (~16s) run;
> a deployed **multi-minute** Workflow re-smoke is still owed and is the only
> way to close acceptance #1 end-to-end. The plan mandates it.

## §6 — Blast radius (Q6)

- `_claude_projects_dir` (`pty_session.py:73`) is the **sole** slug computation
  in `src/` (grep-confirmed). Fixing it repairs all consumers at once:
  resume detection (`_has_prior_session`), session pinning
  (`transcript_reader.py:97`), and Workflow-progress liveness
  (`run_active`/`parse_run_dir`).
- **It fans out past one entity.** `hive_dev` is the only configured name with
  a non-`/.` char — but it is a **maestro**, and a lead under it is
  `hive_dev.<team>` (`lifecycle_manager.py:397`), so every lead/team it spawns
  inherits the underscore in its worktree cwd and is blind. `team_name` is
  user-supplied and **unsanitized**, so any team named with a `_` trips the bug
  under a clean maestro too.
- There is **no entity/team name validation anywhere** in `src/hive`. The slug
  fix makes Hive *agree* with whatever dir CC picks, which fully fixes the
  transcript routing — but names still flow raw into git branches and worktree
  dirs. That is a separate latent risk → **note a name-validation follow-up
  ticket** (out of 030 scope, §design).
- **No test goes red from the fix.** The existing slug test
  (`test_pty_session.py:63`) uses an input where the buggy and correct rules
  agree (`/.` → `--`), so it stays green (its docstring documents the *wrong*
  rule and must be corrected). Pin/heuristic tests already call
  `_claude_projects_dir(tmp_path)` on both sides. Two helpers
  (`test_pty_session.py:186, 217`) build a slug with `.replace("/", "-")` on a
  `tmp_path` that can contain `_` → they must adopt the corrected slug or they
  diverge from `start()`.

## §7 — Adversarial verification summary

5 agents, each tasked to **refute** one claim (run `wf_af47b226-788`):

| Claim | Verdict | Conf. |
|-------|---------|-------|
| Fix regex reproduces CC's rule exactly | confirmed | 0.97 |
| Wrong dir → silent +180s timeout every turn, kills liveness too | confirmed | 0.97 |
| Slug fix alone meets 030 (017 sound) for a normal cwd | confirmed | 0.85 |
| Alarm placement + fix surface + test deltas (design input) | — | — |
| Blast radius (design input) | — | — |

Residual risk carried into the plan: the **deployed multi-minute re-smoke**
(closes the 0.85 gap and the sprint's "behaviour, not deletion" DoD).
