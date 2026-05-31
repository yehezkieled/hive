# Phase 1 — Runtime Migration + Foundational Restructure

**Status:** Ready for implementation  
**Deadline:** 2026-06-15 (Anthropic moves headless `claude -p` to API billing)  
**Scope:** Runtime abstraction + Claude PTY adapter + plan-quota monitoring + `/runtime` command + structural cleanup

---

## Problem Statement

Hive runs a 24/7 fleet of Claude Code agents. Today every turn fires a fresh `claude -p` subprocess — "headless" / print mode. From 2026-06-15, Anthropic bills headless usage at metered API rates. At Hive's workload this means $1000+/month instead of the flat Claude Max subscription (~$100/mo).

Two problems compound this:

1. **Billing path**: Every call goes through `claude -p`. Swapping to interactive PTY sessions (which stay on the Max plan) requires touching `ClaudeSession`, `Entity.build_cli_args()`, and `manager.send_to_entity()` — all intertwined.

2. **Vendor lock-in**: Claude-specific code (`build_cli_args()`, `ClaudeSession`, usage parsing tied to stream-json) is spread across `models/`, `process/`, and `bus/`. There is no harness-agnostic contract. Adding Codex or OpenCode adapters in the future would require editing the same files again.

The fix must solve billing before the deadline and simultaneously lay the foundation so vendor lock-in never returns.

---

## Solution

Introduce a **harness-agnostic `Runtime` interface** — a turn-shaped contract that every harness adapter implements. Quarantine all Claude-specific code inside a `ClaudeAdapter`. Rewrite the adapter internals in two steps so Step 1 is behaviour-neutral and Step 2 is the actual billing fix.

**Two-step de-risked migration:**

- **Step 1 (behaviour-neutral):** Introduce the `Runtime` interface and `ClaudeAdapter`. The adapter wraps the existing `claude -p` subprocess path. `manager.send_to_entity()` is rewired through `Runtime.send_turn()`. The full test suite must stay green after this step. This step has no billing impact but proves the abstraction works end-to-end.

- **Step 2 (billing fix):** Swap `ClaudeAdapter` internals from subprocess-per-turn to a persistent PTY session (`PtySession`). Claude Code runs in an interactive terminal; turns are injected via bracketed paste; output is scraped and cleaned. This is what keeps the Claude Max plan billing active. Step 1's interface and tests are unchanged.

---

## User Stories

1. As the system, I want each entity to send a turn to its assigned harness through a single `send_turn(prompt)` call, so that `manager.py` contains no harness-specific logic.
2. As the system, I want the `Runtime` interface to include `start()`, `stop()`, `is_alive()`, and `send_turn()`, so that lifecycle management is consistent across all future harness adapters.
3. As the system, I want `ClaudeAdapter` to be the only file that imports anything Claude-specific (pty, subprocess args, session IDs, stream-json parsing), so that no Claude logic bleeds into shared code.
4. As the system, I want `ClaudeAdapter` to accept a `ClaudeAdapterConfig` (model, system prompt, tools, permission mode, identity, MCP config), so that entity-level configuration is still expressible without `entity.build_cli_args()`.
5. As the user, I want Step 1 to leave all existing tests green, so that I can review the structural change independently of the PTY internals.
6. As the system, I want `PtySession` to spawn Claude Code in a persistent pseudo-terminal (xterm-256color, 200×50), so that billing stays on the interactive/plan path after Step 2.
7. As the system, I want `PtySession` to inject turn content via bracketed-paste escape sequences (`\x1b[200~…\x1b[201~` + `\r`), chunked above 4096 bytes, so that large prompts are delivered reliably.
8. As the system, I want `PtySession` to detect turn-complete by recognising the Claude Code prompt glyph (`❯` / `>` after a blank line), so that `send_turn()` resolves exactly when Claude has finished responding.
9. As the system, I want `PtySession` to handle Claude's trust prompt automatically (auto-accept within 5 seconds of spawn), so that a new session does not stall on the interactive permission dialogue.
10. As the system, I want `PtySession` to use `--continue` when a prior session `.jsonl` exists for the entity's working directory, so that conversation context is preserved across restarts.
11. As the system, I want `PtySession` to recover from crashes with exponential back-off (max 3 retries, starting at 2s), so that transient terminal failures don't drop a live entity.
12. As the system, I want a turn output parser that strips ANSI escape codes, cursor redraws, spinner frames, and progress lines from raw PTY output, yielding only the assistant's final response text.
13. As the system, I want the existing `parse_actions()` to receive the cleaned response text unchanged, so that `hive_actions` extraction continues to work after the PTY migration.
14. As the system, I want per-turn token counts extracted from the entity's session `.jsonl` transcript (the `usage` field on the last assistant turn), so that `token_store` DB entries remain accurate after stream-json is gone.
15. As the system, I want a `QuotaMonitor` that polls the Anthropic OAuth usage endpoint every 3 minutes, caches the result, and fires a Telegram notification when 7-day rolling usage crosses 80% and again at 90%, so that I am warned before plan exhaustion.
16. As the system, I want `QuotaMonitor` to read the OAuth token from the same `.env` / environment variable that `ClaudeAdapter` uses, so there is no separate secret to manage.
17. As the user, I want a `/runtime <entity> <harness> [model]` Telegram command that assigns a harness to a live entity, so that I can switch an entity without restarting Hive.
18. As the user, I want `/runtime` to accept `claude` as the only valid harness value in Phase 1, with a clear error if any other value is passed, so that the interface is established now even though Codex/OpenCode adapters are not yet built.
19. As the user, I want `/runtime` to trigger a summary handoff when called on a running entity: the current session emits a summary, the new adapter starts with that summary prepended, so that context is not silently lost on a harness switch.
20. As the user, I want `/runtime <entity>` with no harness argument to return the current harness and model for that entity, so I can inspect the fleet without reading logs.
21. As the system, I want entity harness assignment stored in the existing entity row (new `harness` and `model` columns, nullable — default `claude`), so that assignments survive a Hive restart.
22. As the system, I want all `*_store.py` persistence files moved from `bus/` to a new `storage/` package, so that the bus package contains only message-routing logic.
23. As the system, I want `Entity.build_cli_args()` removed from `models/entity.py` after the migration, so that model-layer code contains no process-layer logic.
24. As the developer, I want a `runtime/` package with `__init__.py` exporting `Runtime`, `ClaudeAdapter`, and `PtySession`, so that the new code is easy to discover.
25. As a future developer, I want `docs/architecture/runtime.md` to describe the Runtime interface, the adapter contract, the PTY internals, and the quota-monitoring design, so that building a Codex or OpenCode adapter is self-contained from the docs.
26. As the user, I want `README.md` rewritten to reflect how Hive actually works today (PTY-based, multi-entity fleet, Telegram control), replacing the outdated description.

---

## Implementation Decisions

### Module 1 — `Runtime` interface (`runtime/base.py`)

```python
class Runtime(ABC):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def is_alive(self) -> bool: ...
    async def send_turn(self, prompt: str) -> tuple[str, dict]: ...
    #                                                  ^text  ^usage: {input_tokens, output_tokens}
```

`send_turn` returns `(response_text, usage)`. The usage dict matches the shape currently extracted from stream-json so `_record_usage` and `token_store` need no changes.

### Module 2 — `ClaudeAdapter` (`runtime/claude_adapter.py`)

Step 1 implementation: wraps `ClaudeSession` (existing subprocess). Accepts `ClaudeAdapterConfig` dataclass replacing `entity.build_cli_args()`. Step 2: replaces the `ClaudeSession` dependency with `PtySession` — the `ClaudeAdapter` public API does not change.

### Module 3 — `PtySession` (`runtime/pty_session.py`)

- Spawns via `ptyprocess.PtyProcess.spawn(["claude", ...])` (or stdlib `pty` as fallback)
- Args: `--dangerously-skip-permissions`, `--model`, `--append-system-prompt`, `--continue` if session exists
- Trust-prompt auto-accept: write `\r` if "Do you trust" seen within 5s of spawn
- Inject: bracketed-paste, chunked at 4096 bytes, 300ms flush delay after final chunk
- Turn-complete detection: regex on cleaned output for the idle prompt glyph after a blank line
- Session refresh: after 71 hours, call `stop()` + `start(fresh=False)` (continue mode)
- Crash recovery: `handleExit` → exponential back-off 2s/4s/8s, max 3 retries

### Module 4 — Turn output parser (`runtime/output_parser.py`)

Strip in order:
1. ANSI escape sequences (`\x1b[...m`, `\x1b[...A/B/C/D/K/J`, etc.)
2. Carriage-return rewrites (keep final line per CR group)
3. Known spinner frames (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`, `...`)
4. Claude Code progress lines (lines matching `^\s*(✓|✗|→|·|Bash|Read|Write|Edit)`)

Returns the assistant's final spoken text only. `parse_actions()` receives this output unchanged.

### Module 5 — Usage source (`runtime/usage_reader.py`)

Reads the entity's session `.jsonl` file (path: `~/.claude/projects/<cwd-dashed>/*.jsonl`), finds the last `assistant` turn, extracts `usage.input_tokens` and `usage.output_tokens`. Falls back to `{input_tokens: 0, output_tokens: 0}` if file is missing or malformed — never raises.

### Non-module Phase 1 work

| Item | Location |
|------|----------|
| Carve `runtime/` package | `src/hive/runtime/__init__.py` |
| Move `*_store.py` from `bus/` → `storage/` | `src/hive/storage/` |
| Add `harness`, `model` columns to entities table | `bus/migrations/` |
| Rewire `manager.send_to_entity()` through `Runtime` | `process/manager.py` |
| Remove `Entity.build_cli_args()` | `models/entity.py` |
| `/runtime` command handler | `commands/runtime.py` |
| `QuotaMonitor` background task | `runtime/quota_monitor.py` |
| Rewrite `README.md` | repo root |
| Write `docs/architecture/runtime.md` | `docs/architecture/` |

**Step sequence:**
1. Write `Runtime` base + `ClaudeAdapter` (step 1, wraps subprocess)
2. Rewire `manager.send_to_entity()` — all tests green
3. Move stores to `storage/`, remove `build_cli_args()`
4. Write `PtySession` + output parser + usage reader
5. Swap `ClaudeAdapter` internals to `PtySession` (step 2)
6. Add DB columns + `/runtime` command
7. Add `QuotaMonitor`
8. Docs: `README.md` + `docs/architecture/runtime.md`

---

## Testing Decisions

Each module gets its own test file in `tests/runtime/`.

| Module | File | Coverage target | Strategy |
|--------|------|----------------|----------|
| `Runtime` interface | `test_runtime_base.py` | contract shape | assert abstract methods exist; concrete stub implements correctly |
| `ClaudeAdapter` (step 1) | `test_claude_adapter.py` | high | mock `ClaudeSession`; verify `send_turn` maps args correctly, returns `(text, usage)` |
| `PtySession` | `test_pty_session.py` | **heaviest** | mock `ptyprocess.PtyProcess`; test spawn args, bracketed-paste chunking, trust-prompt auto-accept, turn-complete detection, crash recovery back-off |
| Output parser | `test_output_parser.py` | **heaviest** | property-based tests on ANSI strip; fixture corpus of raw PTY captures → expected clean text |
| Usage reader | `test_usage_reader.py` | high | fixture `.jsonl` files; test last-turn extraction, malformed fallback, missing-file fallback |
| `QuotaMonitor` | `test_quota_monitor.py` | medium | mock HTTP; test threshold triggers, caching, Telegram notify call |
| `/runtime` command | `test_command_runtime.py` | medium | test valid/invalid harness, no-arg inspect, handoff trigger |

Prior art: `tests/process/test_claude_session.py` — use as style reference for async subprocess mocking.

`ClaudeAdapter` step 1 must not cause any existing test to fail. Run the full suite after step 2 rewire before proceeding.

---

## Out of Scope

- **Codex adapter** (Phase 4)
- **OpenCode adapter** (Phase 4)
- **Automatic quota failover** (Phase 4 — needs adapters to exist)
- **Breaking up `manager.py`** (Phase 2 — only `send_to_entity` is touched here)
- **Consolidating the Vault** (Phase 2)
- **`WorkerAgent` → `Worker` rename** (Phase 2)
- **Dismantling `PROJECT_PLAN.md`** (Phase 2)
- **Web dashboard / PWA** (Phase 3)
- **Any Phase 5 features**

The `/runtime` command accepts `claude` only. Harness-selection UI for Codex/OpenCode is deliberately not built here.

---

## Further Notes

**Executor:** This plan is executor-agnostic. It can be implemented by this Claude Code session, by a Hive agent dogfooding (once step 1 ships and the session is stable), or by the user. Step boundaries are natural handoff points.

**Deadline pressure:** June 15 is hard. Steps 1-5 (the PTY swap) are the deadline-critical path. Steps 6-8 (command, quota, docs) are high-value but do not affect billing. If time is short, ship steps 1-5 first.

**PTY library:** `ptyprocess` is the recommended dependency (pure-Python, well-maintained, used by Jupyter and pexpect internally). Add to `pyproject.toml` under `[project.dependencies]`. Fallback: Python stdlib `pty` module (more brittle on Linux, no resize API).

**Stream-json loss:** After step 2, the stream-json `result` event (which currently provides `session_id` and `usage`) is gone. `session_id` is replaced by the persistent PTY process; `usage` is replaced by `.jsonl` transcript reading (Module 5). Both replacements must be in place before step 2 lands.

**Claude session `.jsonl` path:** `~/.claude/projects/<cwd-path-with-dashes>/` — the directory name is the working directory path with `/` replaced by `-`. E.g., `/home/hezki/projects/hive` → `~/.claude/projects/-home-hezki-projects-hive/`. Verify this path at test time; it may differ between Claude Code versions.
