# Plan — Ticket 033: PA Maestro self-identity  (issue #191)

Direct lane (one PR). Make a maestro's system prompt state PA vs. project-maestro,
keyed on `is_pa`. Identity only — no policy/write-fence change. No ADR.

Implementation structure: see `outline.md`. Design + rejected alternatives:
`design.md`. Code grounding: `research.md`.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/models/maestro.py` | modify | 1 — add `is_pa` property (`name == DEFAULT_MAESTRO`) + import |
| `src/hive/process/lifecycle_manager.py` | modify | 1 — use `entity.is_pa` at :311; 2 — set `is_pa` in `_adapter_config_from_entity` |
| `src/hive/runtime/claude_adapter.py` | modify | 2 — add `is_pa` to `ClaudeAdapterConfig`; 3 — append `MAESTRO_IDENTITY` in `_build_pty_system_prompts` |
| `src/hive/process/loops.py` | modify | 3 — add `MAESTRO_IDENTITY` (pa/project) beside `LOOP_PROMPTS` |
| `personalities/role-maestro.md` | modify | 4 — neutralize ownership framing; fix stale "Workers" line |
| `tests/test_entity.py` | modify | 1 — `Maestro.is_pa` unit test |
| `tests/process/test_lifecycle_manager.py` | modify | 2 — extend config-map test for `is_pa` |
| `tests/runtime/test_claude_adapter.py` | modify | 3 — NEW prompt-assembly test (PA vs project) |
| `tests/test_role_jd.py` | modify | 4 — regression guard: old strings gone from `role-maestro.md` |

Not touched: `entity.build_cli_args` / `Maestro.build_cli_args` (dead headless
`claude -p` remnant — deletion deferred to a cleanup ticket; see `design.md`).

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` green (CI runs lint + format as separate gates).
- Build PR closes #191.
- **Live re-smoke** (033 changes live PA behaviour): deploy, then otter
  re-introduces itself as the PA — owns no project, read-any/write-ownerless.
  Confirm a project maestro is unaffected. Smoke from the Tailscale IP, browser
  where JS-rendered.

## Out of scope

- Centralising every scattered PA reference (mapped in `research.md`).
- Write-fence / policy changes (Ticket 024 / ADR 0017).
- Deleting the dead `build_cli_args` (separate cleanup ticket).
- Broad `role-maestro.md` audit beyond the two stale spots.

## Cross-cutting impact

- `personalities/role-maestro.md` is an entity prompt asset, edited inside this
  ticket (the code change is what makes the PA read a correct identity). No
  reference-doc (`README`/`DEPLOYMENT`/`ARCHITECTURE`) edits required.
- `CONTEXT.md`: no new term — **PA Maestro**, **Project ownership**, and
  **Ownership guard** already exist; this ticket makes the prompt communicate an
  already-named identity.

## To build

One branch, one PR that closes #191. Build directly (you or one agent) per
`outline.md`'s ordered steps, then deploy + live re-smoke.
