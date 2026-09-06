# Research — Ticket 012: Curate & expose CC skills to Entities

Code-grounded answers to `questions.md`. File refs are to this repo
unless noted. Confidence is marked: ✓ verified in code/CLI here,
⚠ from CC docs/behaviour (version-sensitive — confirm on the pinned
fleet binary), ✱ design choice deferred to `design.md`.

---

## 1. How an Entity spawns and inherits skills

Flow (PTY path, the only runtime since Ticket 007):

```
LifecycleManager.spawn_*  →  _adapter_config_from_entity()  →  ClaudeAdapter.start()
   (lifecycle_manager.py:109)                                   (claude_adapter.py:96)
        │                                                            │
        ▼                                                            ▼
  ClaudeAdapterConfig                                      PtySession._build_spawn_args()
  (model, allowed_tools,                                   (pty_session.py:107-128)
   disallowed_tools,                                            │
   permission_mode, role…)                                      ▼
                                                  PtyProcess.spawn(args, cwd, dims)
                                                  (pty_session.py:231-235)
```

- The spawned `claude` gets **no `--settings` flag and no `env=` override**
  → it runs on the user's global `~/.claude/` (`pty_session.py:23`,
  `_SETTINGS_PATH = Path.home()/".claude"/"settings.json"`; `PtyProcess.spawn`
  is called with no `env`). ✓
- Therefore every Entity inherits the **full** `~/.claude/skills` library +
  plugin skills + built-ins. No curation today. ✓
- Contrast: the MCP config *is* already isolated per-Entity via
  `--mcp-config <tmpfile> --strict-mcp-config` (`claude_adapter.py:89-93`) —
  precedent that per-spawn config injection is already a pattern here. ✓

## 2. The skill-restriction mechanism (the crux)

CC discovers skills from four sources: built-in (compiled into the
binary), user `~/.claude/skills/`, project `./.claude/skills/`, and
enabled plugins. ⚠

**There is no true allowlist.** ⚠ The permission model:
- `permissions.deny` / `--disallowedTools` with a `Skill(name)` entry
  **removes that skill from the model's context** — it becomes
  unreachable. This is the lever we want.
- `permissions.allow` / `--allowedTools` only **pre-approves** a skill;
  it does **not** hide the unlisted ones. Under Hive's
  `--dangerously-skip-permissions` / bypass modes, unlisted skills stay
  reachable anyway.
- `Skill` denied bare (`--disallowedTools Skill`) removes the entire
  Skill tool. Built-in skills (e.g. `/code-review`, `/loop`) **cannot be
  denied by name** — they ship in the binary. ⚠
- `CLAUDE_CONFIG_DIR` is **undocumented / partially broken** for skill
  isolation — do not rely on it.

> **Consequence for the ticket:** the acceptance line "each role gets
> **only** its curated subset" (allowlist semantics) is **not
> achievable**. The realisable design is a **denylist**: block the
> dangerous skills, leave the rest reachable. This still satisfies "no
> human-interactive or self-recursive skill is reachable — verified" and
> the ticket's own "most, not all". ✱ (Confirm framing in `design.md`.)

CLI flags confirmed present on this host's binary (`claude --help`): ✓
`--allowedTools`/`--disallowedTools`, `--settings <file-or-json>`,
`--setting-sources`, `--append-system-prompt`. Host binary = **2.1.168**;
the fleet may run 2.1.140 (Ticket 009) → the `Skill(name)` deny syntax
must be confirmed on the **pinned** binary. ⚠

## 3. Injection site — reuse existing plumbing

Hive already threads a tool denylist end-to-end:

- `entity.disallowed_tools` → `--disallowedTools` in **both** spawn paths
  (`claude_adapter.py:87-88` for PTY; `entity.py:250` for the legacy
  `build_cli_args`). ✓
- Role tool defaults are written today as a **markdown `## Tools`
  section** in the auto-generated personality, only for maestro/lead
  (`lifecycle_manager.py:69-76`):
  `disallowedTools: Agent Task ExitPlanMode TodoWrite TaskCreate …`
- That markdown section is parsed back into `entity.disallowed_tools` by
  a **regex + whitespace split** (`entity.py:100-103`):
  ```python
  disallowed_str = extract_field(r"disallowedTools:\s*(.+)")
  disallowed_tools = [t.strip() for t in disallowed_str.split() if t.strip()]
  ```

> **Two hard constraints this creates:** ✓
> 1. **Whitespace split** → only **space-free** tokens survive. `Skill(grill-me)`
>    is fine; `Skill(foo *)` would shatter into `Skill(foo` + `*)`. The
>    denylist must be **exact skill names**, no prefix globs.
> 2. **Workers get no `## Tools` section today** → they currently have
>    `Agent`/`Task` *allowed* and zero skill denials. To cover Workers we
>    must add a denial source for them (a worker `## Tools` block, or set
>    the denylist in code regardless of role).

Alternative mechanism (richer, but net-new): pass
`--settings '{"permissions":{"deny":["Skill(a)","Skill(b *)"]}}'` —
supports prefix patterns and dodges the whitespace split, but adds a new
flag path Hive doesn't use. ✱ Weigh in `design.md`; default to the
existing `disallowed_tools` path.

## 4. Role JD system — where the "use these skills" prose goes

- JDs live at `personalities/role-{maestro,lead,worker}.md`, loaded by
  `load_role_jd(role)` (`process/loops.py:15-31`, `lru_cache`) and
  injected as `--append-system-prompt` blocks. ✓
- Prompt assembly order: custom system_prompt → identity → loop text →
  role JD (`claude_adapter.py:62-80`, `entity.py:257-278`). No separate
  base-prompt file. ✓
- **No JD mentions skills today.** ✓ Natural insertion: a new
  `## Skills — when to use` section per JD (after the messaging/org
  sections, before Honesty). Worker JD is short (~61 lines) and has no
  autonomy sections — the block slots after `## Responsibilities`.

## 5. Isolation pattern — and why we likely don't need it

Lona/Wonder isolate via `TELEGRAM_STATE_DIR` exported in wrapper scripts
(`~/.local/bin/claude-{lona,wonder}`); those state dirs hold only plugin
state (`.env`, `access.json`, `inbox/`) — **no `skills/` or
`settings.json`**, so even the bots inherit global skills. ✓ A
per-Entity config-dir with a curated `skills/` subdir is *architecturally*
possible but expensive and fragile (relies on the broken
`CLAUDE_CONFIG_DIR`), and built-ins would persist anyway. ✱ Recommend
**out of scope** — the denylist via `disallowed_tools` gets the safety
win without it.

## 6. Skill risk inventory (per-role hints)

Buckets: **SAFE** (runs to completion headless, no uncontrolled fan-out),
**INTERACTIVE** (hangs on human input), **SELF-RECURSIVE** (spawns
teams/sprints/agent-fleets), **SENSITIVE** (access/secrets/config/external
publish). INTERACTIVE + SELF-RECURSIVE + SENSITIVE are the **deny**
candidates for *all* roles (an Entity never has a human at the keyboard;
the sprint mandates no Entity inherit `build-with-agent-team` /
`plan-next-sprint`).

| Skill | Source | Bucket | Deny? |
|---|---|---|---|
| tdd, test-driven-development | user / superpowers | SAFE | keep |
| diagnose, systematic-debugging | user / superpowers | SAFE | keep |
| research-codebase | user | SAFE (read-only Explore subagent) | keep ✱ |
| code-review, security-review, review | built-in | SAFE | keep (can't deny anyway) |
| handoff, hive-org-stats, zoom-out | user | SAFE | keep |
| verification-before-completion, executing-plans | superpowers | SAFE | keep |
| using-git-worktrees, finishing-a-development-branch | superpowers | SAFE | keep |
| writing-plans, receiving-code-review | superpowers | SAFE | keep |
| edit-article, migrate-to-shoehorn, scaffold-exercises, setup-pre-commit, write-a-skill, to-prd, caveman, remind | user | SAFE | keep |
| grill-me, grill-with-docs, brainstorming, prototype | user / superpowers | INTERACTIVE | **deny** |
| capture, curate, improve-codebase-architecture, triage | user | INTERACTIVE | **deny** |
| requesting-code-review | superpowers | INTERACTIVE-ish (1 subagent) | **deny ✱** |
| build-with-agent-team, dispatching-parallel-agents | user / superpowers | SELF-RECURSIVE | **deny** |
| subagent-driven-development, tdd-agentic | superpowers / user | SELF-RECURSIVE | **deny** |
| plan-next-sprint, run-ticket, to-issues, initiate-project | user | SELF-RECURSIVE | **deny** |
| cc-freeze, git-guardrails-claude-code, git-ship | user | SENSITIVE | **deny** |
| telegram:access, telegram:configure, update-config | plugin / built-in | SENSITIVE | **deny** |
| agent-teams, loop, schedule | built-in/CLI | SELF-RECURSIVE/SENSITIVE | **deny if deniable** ⚠ |

✱ borderline calls (`research-codebase`, `requesting-code-review`) and
the exact final list are resolved in the grill → `design.md`.

## 7. Residual risks / verify items

- **Built-ins can't be denied** (`/code-review`, `/loop`, `/schedule`,
  `/run`, `agent-teams`?). ⚠ Need an empirical check on the **pinned
  fleet binary**: which dangerous names are built-in (undeniable) vs
  deniable skills? `/loop`, `/schedule`, `agent-teams`, `update-config`
  are the ones to probe.
- **`Skill(name)` deny syntax** must work on 2.1.140 (fleet), not just
  2.1.168 (dev). ⚠
- **Denylist rot:** a new skill added to `~/.claude/skills` defaults to
  *reachable* (denylist gap). Mitigation options → `design.md`.

## 8. Open design forks (→ design.md / grill)

1. Accept **denylist** semantics (vs. chasing an unachievable allowlist)?
2. **Global** safety denylist + per-role JD prompting, or per-role
   denylists too?
3. Injection: extend the **`## Tools` markdown** path (exact names only)
   vs. new **`--settings deny`** path (richer)?
4. Worker coverage: add a worker `## Tools` block vs. set denylist in
   code for all roles.
5. Final per-role allowed/denied list (resolve the borderline skills).
6. Verification: mocked-PTY unit test (CI) + documented host smoke.
7. Direct lane vs fan-out; config-dir isolation in/out of scope.
