# Research — diagnosis of the PTY interactive-gate hang

Investigated 2026-05-29. Everything below is verified against prod
logs, the postgres state, and real transcript files.

## Symptom

User reported: `/m:dev hi` on Telegram gave **no reply**, but the same
kind of message on the **web dashboard** worked.

## What actually happened (evidence)

`messages` table around the incident:

```
11:41:09  user → otter   "hi"            ┐ WEB    — otter is YOLO  → replied <1s ✅
11:41:09  otter → user   "Hi Hezki..."   ┘
11:43:53  user → dev     "hi"            ┐ TELEGRAM — dev is PLAN  → empty/err ❌
11:43:53  dev → user     ""  (empty)     ┘
```

So it was **never a Telegram-vs-web transport problem**. The user
messaged `otter` (yolo) on web and `dev` (plan) on Telegram. The
divergence is the **maestro's permission mode**.

`journalctl` for the hung turn:

```
11:40:45 Received from Telegram: /m:dev hi
11:40:45 PtySession: spawning claude --model opus --permission-mode plan
...
11:43:51 [hive.commands.dispatch] ERROR: Error sending to dev
         TimeoutError: No completed assistant turn in <...>.jsonl within 180.0s
```

postgres `entities`:

```
dev      | maestro | plan      ← hangs
otter    | maestro | yolo      ← works
hive_dev | maestro | yolo
```

## Root cause

In plan mode, Claude Code writes a plan then shows an interactive
menu (`Would you like to proceed? 1. Yes / 2. No`). The PTY harness
has **no handler** for it — `pty_session.py:_handle_trust_prompt`
only answers the *startup* trust dialog. Nothing presses a key, so no
completed assistant turn is ever written, and
`TranscriptReader.await_next_assistant_turn` (timeout 180s) raises.

The hung session's transcript (`24feed97-…jsonl`) shows the maestro
also called **`AskUserQuestion`** (tool_use with no tool_result) — a
second interactive gate with the same failure mode. So this is a
**class of bug** ("interactive gates hang the PTY"), not just plan
mode.

Why PTY-specific: headless `claude -p --permission-mode plan` printed
the plan and exited (turn completed). The interactive PTY blocks on
the menu.

## Key code paths

- `src/hive/runtime/pty_session.py`
  - `_build_spawn_args` (line ~64): maps `permission_mode` → CLI flags.
    `bypassPermissions`/dangerous → `--dangerously-skip-permissions`;
    else → `--permission-mode <mode>`.
  - `start()` / `_handle_trust_prompt()` (line ~297): only handles the
    startup trust prompt. **No plan/AskUserQuestion handler exists.**
  - `send()` (line ~184): `_inject(prompt)` →
    `identify_session` → `await_next_assistant_turn(timeout=180.0)`.
  - `_inject()` (line ~348): how keystrokes are written to the PTY —
    this is where an approval keypress ("1\r") would be injected.
- `src/hive/runtime/transcript_reader.py`
  - `await_next_assistant_turn`: polls the `.jsonl` for a NEW
    `type=="assistant"` entry that has been quiescent for
    `quiescence_ms`. This is where gate-detection hooks in.
- `src/hive/process/manager.py`
  - `send_to_entity` (line ~643) → `_get_or_create_adapter`; spawn
    uses `entity.permission_mode` (lines 170, 1351, 1429).
  - `_notify(...)` (line ~2158): the path that actually reaches
    Telegram (bridge is registered on the notification_dispatcher in
    `__main__.py:299`). **Entity→user replies reach Telegram via
    `_notify`, NOT via `router.route`.**
- `src/hive/bus/router.py`
  - `route()` logs to the store (→ web sees it) and delivers to a
    queue only if the recipient is registered. `"user"` is **never**
    registered, so `route(recipient="user")` always logs-but-drops
    with the "No queue for recipient user" warning. Harmless, but it
    means the web and Telegram reply paths are genuinely different.

## Transcript shape for gate detection (verified)

Plan-mode entry in the `.jsonl` is an **attachment**:

```json
{"type":"attachment",
 "attachment":{"type":"plan_mode","planFilePath":
   "/home/hezki/.claude/plans/<slug>.md","planExists":false},
 "sessionId":"...","timestamp":"..."}
```

So the plan-mode gate is detectable structurally (no screen-scraping):
watch for an `attachment.type=="plan_mode"`, and/or a
`tool_use` named `ExitPlanMode` / `AskUserQuestion` that has no
matching `tool_result`. The finalized plan markdown lands at
`planFilePath` when the maestro exits plan mode.

NOTE: do not match the bare string `ExitPlanMode` — it appears in the
`deferred_tools_delta` available-tools list (false positive). Match on
an actual `tool_use` block with that `name`.

## Existing pattern to reuse

Hive already has a **pending-approval round-trip**:
- `request_mode_change` → approval row → `/approve` `/deny` (Telegram)
  and `POST /api/mode-request/{id}/approve` (web).
- Vault actions use the same shape
  (`POST /api/vault-action/{id}/approve`).

The interaction bridge should almost certainly model a plan-approval
as another pending row reusing this pattern, rather than inventing a
new one. See `src/hive/commands/dispatch.py` (`_execute_approve`,
`_execute_deny`) and `src/hive/web/app.py` (mode-request endpoints).

## #26 permission-prompt gate — capture findings (2026-06-01)

**Question:** is a tool-permission prompt ("Allow `Bash(rm …)`? Yes/No")
structurally detectable in the `.jsonl` like the plan/ask gates?
**Finding: no.** #26 is deferred per its own acceptance criteria; decision in
[ADR 0005](../../adr/0005-permission-gate-not-transcript-detectable.md).

**What was checked**
- 638 existing transcripts under `~/.claude/projects/`: **zero** permission
  prompts — Hive Entities run `bypassPermissions`/`yolo`, so tools never
  prompt. The only permission-related markers are `type:"permission-mode"`
  (the *mode* setting) and a `command_permissions` attachment, which is a
  **config snapshot** (`{"type":"command_permissions","allowedTools":[…]}`),
  not a prompt.
- Three live captures (`claude --permission-mode default`): a safe command
  (`echo`) is **auto-allowed** and resolves `tool_use → tool_result` with no
  permission event. No `permission_request` / `can_use_tool` / `approve`
  token exists anywhere in the schema.

**Why it's not detectable**
- Plan/ask gates carry a **unique tool name** (`ExitPlanMode`,
  `AskUserQuestion`) → an unanswered one is unambiguous.
- A permission prompt fires on an **ordinary** tool (`Bash`/`Write`/`Edit`/…).
  Parked, it is just an **unmatched `tool_use`** for that ordinary tool —
  structurally identical to a tool that is simply still executing. There is no
  separate "awaiting permission" event; the prompt is a client-side TUI
  overlay not written to the transcript.
- The only guess would be a **time-based heuristic** ("ordinary `tool_use`
  unmatched for > N s") — false-positives on slow legitimate calls, and
  exactly the screen-state guessing ADR 0001 / the design rejects.

**Practical note:** with `bypassPermissions` the prompt never arises anyway;
the sprint scoped this out ("beyond what yolo/bypassPermissions already
cover"). If permission gating is ever needed, the heuristic above is the only
viable path and must be a deliberate, separately-scoped decision.
