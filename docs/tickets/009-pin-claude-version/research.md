# Research — Ticket 009

Code + host evidence behind the design. Anchors re-verified against `main` at
`90c78e7`.

## Host: two independent installs, fleet silently picks the stale one

```
Native (dev):  ~/.local/bin/claude → ~/.local/share/claude/versions/2.1.162
               self-updating (2.1.160 → 161 → 162 all landed 2026-06-02..04)
npm  (fleet):  /usr/bin/claude     → @anthropic-ai/claude-code  2.1.140
               frozen since 2026-05-13 (only moves on a manual `npm i -g`)
```

- An interactive shell resolves `~/.local/bin/claude` first → **2.1.162**.
- `hive.service` resolves **2.1.140** because of the PATH below.

## Why the fleet gets the npm one

`hive.service` (host-only at `~/.config/systemd/user/hive.service`, **not in
git**) sets **no `Environment=` / `PATH=` line** — only
`EnvironmentFile=…/.env`. So the service inherits the systemd user-manager's
default PATH, which **omits `~/.local/bin`**. `which claude` therefore falls
through to `/usr/bin/claude` (npm, 2.1.140).

## How the binary is resolved at spawn

- `src/hive/runtime/pty_session.py:71` — `_build_spawn_args()` hardcodes
  `args = ["claude", "--model", model]`. `argv[0]` is the **bare literal
  `"claude"`** — never an absolute path, never a config value.
- `src/hive/runtime/pty_session.py:177` — `PtyProcess.spawn(args, cwd=…,
  dimensions=…)` is called **with no `env=`**. ptyprocess defaults `env=None` →
  `os.execv`, which carries the parent (service) environment + PATH straight
  through. PATH resolution (`which(argv[0])`) also runs against that inherited
  PATH.
- Net: the binary that runs is **whatever `which claude` finds on the service's
  PATH** — i.e. the npm 2.1.140. This is the root cause.

## No version is logged

- The only spawn-time log is `pty_session.py:176`:
  `logger.info("PtySession: spawning %s", " ".join(args[:5]))` — it prints the
  first 5 argv tokens (`claude --model <model> <first-flag>`) and stops. It
  logs neither the resolved binary path nor its version.
- A whole-tree search finds **no `claude --version` capture anywhere**. There is
  currently no way to know which CC version a Turn actually ran on.

## Why this is dangerous, not cosmetic

The PTY adapter **scrapes the Claude Code TUI** to detect interactive gates and
turn completion; [ADR 0001](../../adr/0001-harness-agnostic-runtime.md) flags it
as "sensitive to Claude Code TUI changes." Running 2.1.140 in the fleet while
testing against 2.1.162 means a TUI change could break gate-detection in
production and never show in dev — and the gap only widens (npm never
auto-updates). `claude doctor` inspects the native install, not the npm one, so
the drift is invisible to it.

## Config + logging patterns (where the fix lands)

- **Config style** — `src/hive/config.py` is flat module-level constants read at
  import after `load_dotenv()` (`config.py:9,52`). There is already a
  **"Claude CLI defaults"** block at `config.py:87-89`
  (`DEFAULT_MODEL = os.environ.get("HIVE_DEFAULT_MODEL", "opus")`,
  `MAX_CONCURRENT_SESSIONS = …`). A `HIVE_CLAUDE_BINARY` constant slots in
  there exactly. (`VaultConfig` from ticket 005 is the lone grouped dataclass;
  a single binary-path string does not warrant a value object.)
- **Logging** — configured once in `__main__.py`
  (`%(asctime)s [%(name)s] %(levelname)s: %(message)s`); modules use
  `logging.getLogger(__name__)`. A version line at `pty_session.py:176` lands
  under `hive.runtime.pty_session`.
- **Test seam** — `tests/conftest.py` has an autouse guard that fails any test
  reaching a real PTY adapter (forces a Fake). The version-resolution helper
  must therefore be import-safe and side-effect-free at import (no subprocess /
  no FS access until called), so tests don't trip the guard or shell out.

## The Advisor is a *second* spawn site — handled by Ticket 013

- `src/hive/mcp/advisor_server.py:139` spawns a one-shot
  `claude -p --model opus` (Hive's **custom** advisor MCP tool) with the same
  bare `"claude"`, inheriting the same broken PATH → also runs 2.1.140.
- **Finding:** Claude Code now ships a **native `/advisor`** (since **2.1.101**)
  that runs Opus **in-process** — no separate `claude` spawn, no MCP. Hive's
  custom advisor duplicates it. Rather than patch the custom advisor's
  resolution, Ticket [013](../013-retire-custom-advisor/) retires it for the
  native one, which **deletes this spawn site**. So 009 deliberately leaves the
  advisor alone; fixing the harness (below) covers everything 009 owns.
- Lower-risk anyway: the advisor is headless `claude -p --output-format
  stream-json` (no TUI scraping), so version drift is far less likely to break
  it than the harness.
