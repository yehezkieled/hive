# Design — Ticket 030

Chosen approach, grounded in [`research.md`](research.md). Two parts: a
**root-cause slug fix** and a **fail-loud guard** (the user chose fix + alarm).
Direct lane — one module, one PR.

## Decision

Match Claude Code's project-dir slug rule exactly, and make a future drift in
that rule **loud instead of silent**.

### Part 1 — the slug fix (root cause)

`src/hive/runtime/pty_session.py:73`:

```python
-    slug = str(cwd).replace("/", "-").replace(".", "-")
+    # Claude Code's slug rule (verified against the live binary's on-disk
+    # project dirs, 2.1.177): replace EVERY non-alphanumeric char with '-',
+    # with NO collapsing of runs — so '/.' → '--' and '_' → '-'. The old rule
+    # only rewrote '/' and '.', silently keeping '_' (and other punctuation),
+    # which mis-located the transcript dir for any cwd with an underscore
+    # (e.g. a 'hive_dev' maestro worktree) → a 180s phantom timeout (Ticket 030).
+    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))
```

Update the docstring (`pty_session.py:66-72`) to state the real rule. This is
the **only** slug computation in `src/`; fixing it repairs resume detection,
session pinning, and Workflow-progress liveness together (research §6).

### Part 2 — the fail-loud guard (the alarm)

The bug survived because a wrong slug **fails silently** — it looks like a slow
model and dead-ends in a 180s timeout. Add a guard that names the root cause in
the journal within seconds.

**Placement:** inside `send()`'s first-resolution block (`if self._session_path
is None:`), **after** `resolve_session` and **before** the await-loop. By that
point `_inject` has already given Claude Code input, so CC has been triggered to
lazily create the projects dir — meaning a correct slug's dir *must* appear
shortly, while a wrong slug's dir *never* will.

```python
# Fail-loud slug-drift guard (Ticket 030). The phantom-180s signature is a
# pinned transcript path under a projects dir Claude Code never creates because
# our slug rule drifted from CC's. CC creates the dir lazily on first input
# (which _inject already gave), so poll the PARENT dir briefly to absorb the
# lazy-create lag, then ERROR loudly instead of silently eating a 180s timeout.
projects_dir = self._session_path.parent
grace_deadline = time.monotonic() + _SLUG_GUARD_GRACE_S
while not projects_dir.is_dir():
    if time.monotonic() >= grace_deadline:
        logger.error(
            "PtySession: Claude Code projects dir %s does not exist %.0fs after "
            "first input for cwd %s — the transcript-slug rule has likely "
            "drifted from Claude Code's (see _claude_projects_dir). This turn "
            "will dead-end in a phantom no-progress timeout.",
            projects_dir, _SLUG_GUARD_GRACE_S, self._cwd,
        )
        break
    await asyncio.sleep(0.2)
```

with a constant near the other timing constants:

```python
# Grace for Claude Code to lazily create the projects dir after first input
# before the slug-drift guard alarms (Ticket 030). Generous vs CC's sub-second
# create so a slow disk never false-fires.
_SLUG_GUARD_GRACE_S = 5.0
```

**Why it can't false-fire on the healthy path:** runs once per (re)spawn (the
`is None` gate); only *after* `_inject` has triggered CC; checks the **parent**
projects dir, not the laggier per-session `<uuid>.jsonl`; and waits a 5s grace
that CC's sub-second create clears comfortably.

**Why it logs-and-continues (`break`, not `raise`):** the alarm's job is
**visibility**, not recovery. Raising would convert a slow-disk false positive
into a hard turn failure; instead the real 180s timeout + auto-bounce (Ticket
020) remain the safety net, while the ERROR pins the root cause at the 5s mark.

## Alternatives considered (rejected)

| Alternative | Why rejected |
|-------------|--------------|
| **Guard at `start()`** (check dir exists at spawn) | False-fires on every brand-new clean cwd — CC hasn't been given input yet, so the dir legitimately doesn't exist. Must be *after* `_inject`. |
| **Derive the dir from CC's session-state file** instead of computing the slug | Bigger change, and `_has_prior_session` + the pre-spawn `before_sizes` snapshot run *before* any pid/session-state exists, so a slug is still needed. Doesn't remove the dependency it claims to. |
| **`raise` on the missing dir** (fail-fast) | Turns a pathological slow-disk lag into a hard failure; loses the auto-bounce safety net. Visibility is enough. |
| **Sanitize entity/team names** so cwds never contain `_` | Treats a symptom on top of the root cause; the slug must match CC regardless of naming. (Still worth a *separate* hardening ticket — see below.) |

## ADR? — No

This is a **bugfix** that aligns Hive with an external tool's existing
behaviour, not a new architectural decision. The turn-acceptance / Workflow-
liveness design is already owned by **ADR 0014** (Ticket 017), and 030
*confirms* that design was correct — the defect was the slug, upstream of it.
The CC slug rule is captured as a code comment (an external-dependency
assumption, like the file-layout notes in ADR 0010/0014). No new ADR; no
`CONTEXT.md` term added.

## Test plan

`tests/runtime/test_pty_session.py`:

- **Rename + augment** `test_claude_projects_dir_replaces_slashes_and_dots`
  → `…_replaces_all_nonalnum`: keep the `/.` → `--` case, fix the docstring,
  **add an underscore assertion** (the regression that was missing).
- **Add** `test_claude_projects_dir_replaces_underscore` pinning the exact smoke
  cwd → dir (`hive_dev.smoke` → `…-hive-dev-smoke`).
- **Fix helpers** at lines ~186 and ~217: replace `str(tmp_path).replace("/",
  "-")` with `_claude_projects_dir(tmp_path)` (already imported) — pytest
  `tmp_path` names contain `_`, so the buggy helper would build a dir `start()`
  never globs.
- **Add** `test_send_alarms_when_projects_dir_missing` (missing dir + pinned
  session-id + short grace → assert an ERROR mentioning "slug"/"does not exist").
- **Add** `test_send_no_alarm_on_lazy_creation` (dir appears within the grace
  window → assert **no** ERROR) — guards the healthy path against a false-fire.

## Verification (in the build PR)

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- Full `pytest -m "not integration"` green (run with `PYTHONPATH=src` from the
  worktree — the shared editable install pins to MAIN's `src/`).
- **Deployed re-smoke (required — closes the 0.85 residual):** drive a maestro
  → lead → multi-minute Workflow turn on deployed code, under **both** an
  **underscore-named** entity (e.g. a `hive_dev` lead — proves the fix) **and** a
  **clean-named** one (e.g. an `otter` lead — proves no independent 017 gap).
  The turn must be **accepted on the sentinel**, not false-timed-out.

## Reference-doc impact & follow-up

- **No** `CONTEXT.md` / ADR edits (bugfix; rule captured in code comment).
- **Follow-up ticket to note (not build here):** entity/team **name
  validation** — names flow raw into git branch names, worktree dirs, and
  addressing; a name with a slash/space/shell-meta could break worktree or
  branch creation independently of the slug. Restrict to `[A-Za-z0-9._-]` at
  spawn. Out of 030 scope.

## Non-goals (reaffirmed)

- Auto-bouncing / restarting a jammed session — **Ticket 020**.
- Steering a running Workflow — S7.
- Changing the 017 liveness path — it is sound; the slug fix is sufficient.
