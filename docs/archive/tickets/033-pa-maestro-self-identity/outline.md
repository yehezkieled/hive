# 033 — Outline

Implementation structure for the chosen design (`design.md`). Single PR.
Ordered so each step compiles/tests on its own.

## Step 1 — `Maestro.is_pa` property (source of truth)

- `src/hive/models/maestro.py`: add `from hive.config import DEFAULT_MAESTRO`
  and an `is_pa` `@property` (`self.name == DEFAULT_MAESTRO`).
- `src/hive/process/lifecycle_manager.py:311`: replace the inline
  `entity.name == DEFAULT_MAESTRO` with `entity.is_pa` (entity is a `Maestro`
  in that branch). Keep behaviour identical — same source value.
- Test: `tests/test_entity.py` — `is_pa` True for `DEFAULT_MAESTRO`, False otherwise.

## Step 2 — carry `is_pa` onto the adapter config

- `src/hive/runtime/claude_adapter.py`: add `is_pa: bool = False` to
  `ClaudeAdapterConfig`.
- `src/hive/process/lifecycle_manager.py:_adapter_config_from_entity`: set
  `is_pa=getattr(entity, "is_pa", False)`.
- Test: extend `test_adapter_config_maps_entity_fields`
  (`tests/process/test_lifecycle_manager.py`) — `config.is_pa` reflects the entity.

## Step 3 — identity block + append in the live builder

- `src/hive/process/loops.py`: add `MAESTRO_IDENTITY: dict[str, str]` with
  `"pa"` and `"project"` variants, beside `LOOP_PROMPTS`.
- `src/hive/runtime/claude_adapter.py:_build_pty_system_prompts`: import
  `MAESTRO_IDENTITY`; when `cfg.role == "maestro"`, append
  `MAESTRO_IDENTITY["pa" if cfg.is_pa else "project"]` after the role JD.
- Test (the important one): NEW in `tests/runtime/test_claude_adapter.py` —
  - `is_pa=True` config → prompts contain the PA marker, not the project marker.
  - `is_pa=False` config → the reverse.
  - assert on stable phrase markers ("PA Maestro" / "project maestro"),
    not the whole string.

## Step 4 — neutralize `role-maestro.md`

- `personalities/role-maestro.md`:
  - Reword the opening (lines ~3–5) so it no longer asserts project ownership
    ("decide what teams the project needs" / "a maestro owns a project") —
    ownership-neutral; the appended block states which kind.
  - Fix line ~7 "Workers do the actual coding" → Leaf-agent / Workflow-run
    language consistent with `CONTEXT.md`.
- Test (regression guard): assert `role-maestro.md` no longer contains the old
  ownership strings nor "Workers do the actual coding". Co-locate with the
  existing role-file sanity checks in `tests/test_role_jd.py`
  (`TestRepoLevelRoleFiles`).

## Step 5 — verify

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` green.
- Deploy + live re-smoke: otter re-introduces itself as the PA (owns no project,
  read-any/write-ownerless); a project maestro is unaffected.

## Touch list

| Path | Step |
|------|------|
| `src/hive/models/maestro.py` | 1 |
| `src/hive/process/lifecycle_manager.py` | 1, 2 |
| `src/hive/runtime/claude_adapter.py` | 2, 3 |
| `src/hive/process/loops.py` | 3 |
| `personalities/role-maestro.md` | 4 |
| `tests/test_entity.py` | 1 |
| `tests/process/test_lifecycle_manager.py` | 2 |
| `tests/runtime/test_claude_adapter.py` | 3 |
| `tests/test_role_jd.py` | 4 |

Not touched: `entity.build_cli_args` / `Maestro.build_cli_args` (dead — see
`design.md` follow-up note).
