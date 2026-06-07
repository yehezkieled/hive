# Design — Ticket 009

## Chosen approach

**Resolve the harness `claude` explicitly from config, point it at the native
self-updating install, and log the resolved version on every spawn.**

Three parts:

1. **Explicit resolution (config absolute path).** Add a flat constant to the
   "Claude CLI defaults" block in `src/hive/config.py`:

   ```python
   CLAUDE_BINARY = os.path.expanduser(
       os.environ.get("HIVE_CLAUDE_BINARY", "claude")
   )
   ```

   Replace the bare literal at `pty_session.py:71`
   (`args = ["claude", "--model", model]`) with `args = [CLAUDE_BINARY, …]`.
   Default stays `"claude"` (back-compatible: behaves as today if unset).

2. **Policy = track-latest, set in `.env`.** On the host, set
   `HIVE_CLAUDE_BINARY=/home/hezki/.local/bin/claude` — the native **symlink**.
   It re-points on each self-update, so the fleet runs exactly the version dev
   develops/tests against, automatically. No promotion ritual.

3. **Per-spawn version logging.** At `pty_session.py:176`, alongside the
   existing "spawning" line, log the resolved binary + its version every spawn
   (instant-follow). Read the version cheaply by resolving the symlink target
   (`os.path.realpath` → `…/versions/2.1.162` → basename `2.1.162`); fall back
   to a `claude --version` subprocess if the path isn't a recognizable version
   string. The log line then always reflects what actually ran.

   ```
   …[hive.runtime.pty_session] INFO: spawning worker-3 on claude 2.1.162 (/home/hezki/.local/share/claude/versions/2.1.162)
   ```

**Cross-cutting:** `docs/DEPLOYMENT.md` gains a short "Claude Code version
policy" note (the knob + track-latest + how to freeze if ever needed). **No
systemd unit edit** — the config absolute path makes the PATH irrelevant, so the
`.service` file is untouched (only the host `.env` gets one line).

## Why these choices (the forks we resolved)

### Config absolute path, not a systemd PATH fix
The acceptance demands resolution "not via an ambiguous PATH lookup." A PATH fix
(prepend `~/.local/bin`) is *still* a PATH lookup and edits untracked host
state. The config path is explicit, lives in the repo, is testable, and — key
insight — the **same knob expresses either policy**: point at the symlink →
track-latest; point at a `versions/X` file → freeze. Policy becomes one `.env`
value, zero extra code.

### Track-latest, not freeze (despite the ticket title "Pin")
Goal: the fleet runs the version we actually test. Pointing at the native
symlink makes dev and fleet **the same install**, always — satisfying the
acceptance's "share one install" branch with no upkeep. The residual risk (an
uncontrolled self-update breaking the TUI scraper) is **bounded**: dev runs the
identical binary so breakage shows in dev too, and the version is now logged, so
it's diagnosable in the journal. A true *freeze* was the alternative — but the
native installer **prunes old `versions/X` files**, so freezing would force a
move to npm (stable path, manual `npm i -g` bumps) plus a promotion process and
a managed dev-ahead-of-fleet gap. Not worth it for a personal-fleet deployment;
"capability over ceremony." (This is the one surprising call — the title says
"Pin" but we track. If the policy ever proves contentious, graduate this section
to an ADR.)

### Read the version every spawn, not once at startup
Both are honest; the difference only shows in the window between a dev
self-update and the next service restart. Every-spawn gives **instant follow**
(the fleet is never even briefly behind dev) at the cost of a cheap resolve per
spawn — negligible against the seconds a PTY boot already takes. Read-once was
the alternative (consistent per deployment, restart-granularity); we preferred
instant follow.

### Advisor spawn left to Ticket 013, PATH net dropped
The Advisor's `claude -p` (`advisor_server.py:139`) is a second drift site, but
it's a **nested** spawn — fixing the harness `argv[0]` can't reach it. Rather
than patch soon-to-be-deleted code, Ticket
[013](../013-retire-custom-advisor/) retires the custom advisor for CC's native
`/advisor`, removing the spawn entirely. With it gone, the harness is Hive's
only `claude` spawn (and it uses the absolute path), so the systemd PATH safety
net has no remaining beneficiary — **dropped**, keeping 009 minimal.

## Rejected alternatives

| Option | Why not |
|--------|---------|
| Systemd PATH fix (prepend `~/.local/bin`) | Still a PATH lookup; edits untracked host unit; acceptance disfavors it. Kept only as a (dropped) safety net. |
| Freeze a `versions/X` native file | Native installer prunes old versions → the absolute path can break (`FileNotFound`). |
| Freeze on npm + promotion flow | Reintroduces a managed dev-ahead-of-fleet gap + manual bumps; over-ceremony here. |
| Read version once at startup | Loses instant-follow; we chose per-spawn. |
| Route the Advisor through the constant now | Touches code that Ticket 013 deletes; the PATH net would be needed for the nested spawn anyway. |

## Acceptance mapping
- *Single, known version, deterministically* → config absolute path (1).
- *Version logged at spawn* → per-spawn version log (3).
- *Dev & fleet share one install, no silent drift* → native symlink (2).
- *Deploy runbook records the policy* → `DEPLOYMENT.md` note (cross-cutting).
