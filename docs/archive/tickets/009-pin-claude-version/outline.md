# Outline — Ticket 009

Implementation structure. The change is small; the only non-trivial piece is the
version-resolution helper.

## 1. `src/hive/config.py` — the binary constant
Add to the "Claude CLI defaults" block (after line 89):
```python
CLAUDE_BINARY = os.path.expanduser(os.environ.get("HIVE_CLAUDE_BINARY", "claude"))
```
Import-time, flat constant, matches the existing pattern. `expanduser` so a
`~`-style value works; default `"claude"` keeps current behavior when unset.

## 2. Version-resolution helper (in `runtime/pty_session.py`)
A module-level function, side-effect-free until called (so the conftest PTY
guard and import stay clean):
```python
def _resolve_claude_version(binary: str) -> tuple[str, str]:
    """Return (resolved_path, version). Cheap path first, subprocess fallback."""
    resolved = os.path.realpath(binary)            # symlink → versions/X
    version = os.path.basename(resolved)
    if not _looks_like_version(version):           # e.g. not 'x.y.z'
        version = _claude_version_subprocess(binary)  # `claude --version`, short timeout
    return resolved, version
```
- `_looks_like_version`: simple `N.N.N` check (regex).
- `_claude_version_subprocess`: `subprocess.run([binary, "--version"], …)` with
  a short timeout + graceful fallback to `"unknown"` on failure (never block or
  crash a spawn over a version probe).

## 3. `runtime/pty_session.py` — use the constant + log the version
- `_build_spawn_args` (line 71): `args = [CLAUDE_BINARY, "--model", model]`
  (import `CLAUDE_BINARY` from `hive.config`).
- At the spawn site (line ~176), after the existing "spawning" log, add:
  ```python
  path, ver = _resolve_claude_version(CLAUDE_BINARY)
  logger.info("PtySession: %s on claude %s (%s)", entity_label, ver, path)
  ```
  (Resolve once per spawn — instant-follow.)

## 4. `docs/DEPLOYMENT.md` — version policy note (cross-cutting)
Short subsection under the Claude Code prerequisite: the `HIVE_CLAUDE_BINARY`
knob, the track-latest default (point at `~/.local/bin/claude`), how to freeze
if ever needed (npm + pin), and that the version is logged at spawn.

## 5. Host `.env` (not in git)
Add `HIVE_CLAUDE_BINARY=/home/hezki/.local/bin/claude`. Documented in
`DEPLOYMENT.md`; applied on deploy.

## Tests
- Unit-test `_resolve_claude_version`: symlink-target happy path returns the
  basename version; non-version path triggers the subprocess fallback (mock
  `subprocess.run`); subprocess failure → `"unknown"`. No real `claude` spawned
  (respects the conftest guard).
- Assert `_build_spawn_args` uses `CLAUDE_BINARY` (monkeypatch the env →
  argv[0] changes).
