# Outline — Ticket 024: Project ownership & PA write-policy

Module sketch for the two slices in [`design.md`](design.md). Paths are
`src/hive/`-relative unless noted. New files marked **NEW**.

## Slice A — registry + ownership (postgres CRUD, no spawn changes)

```
models/project.py            **NEW**  Project dataclass + ProjectOwnershipError
  Project(name, root_path, owning_maestro: str|None, created_at, updated_at)
  ProjectOwnershipError(Exception)   # raised on 2nd-maestro / shared-project

bus/migrations/029_projects.sql  **NEW**
  CREATE TABLE projects(
    name           text PRIMARY KEY,
    root_path      text NOT NULL UNIQUE,
    owning_maestro text REFERENCES entities(name) ON DELETE SET NULL,
    created_at timestamptz, updated_at timestamptz)
  CREATE UNIQUE INDEX ON projects(owning_maestro) WHERE owning_maestro IS NOT NULL

bus/project_store.py         **NEW**  ProjectStore(pool)  — mirror entity_store.py
  upsert / load(name) / by_root_path / owned_roots() / for_maestro(name)
  / all() / delete(name)

bootstrap.py:34-79           wire ProjectStore(pool) into build_process_manager,
                             pass into ProcessManager beside EntityStore

process/lifecycle_manager.py
  register_maestro (188-228)  if a project is named → assign + ownership check
  assign_project(name, maestro) **NEW**  the ≤1-maestro gate → ProjectOwnershipError

commands/dispatch.py         /project new <name> <path> [maestro]
                             /project assign <name> <maestro>
                             /project list        (mirror an _execute_* + table entry)

tests/
  bus/test_project_store.py           upsert/load/owned_roots/uniqueness
  process/test_project_ownership.py   2nd-maestro rejected; assign happy path
```

**Delivers acceptance #1–2.** No spawn/adapter changes → no deployed re-smoke
risk.

## Slice B — ownership guard + project home (the fence; depends on A)

```
hooks/__init__.py            **NEW**
hooks/ownership_guard.py     **NEW**  the PreToolUse guard
  stdin → tool_input.file_path
  load writable policy (HIVE_ENTITY + ownership snapshot, see below)
  project maestro: allow iff abspath under own root
  PA (name == DEFAULT_MAESTRO): deny iff under any owned root, else allow
  block = print(reason, file=stderr); sys.exit(2)   else sys.exit(0)

process/ownership_policy.py  **NEW**  pure resolver (parallel to tool_policy.py)
  writable_policy(entity, projects) -> WritablePolicy
  settings_payload(policy) -> dict        # the hooks.PreToolUse block

process/lifecycle_manager.py
  _adapter_config_from_entity (96-120)  build per-spawn settings.json from
                                        settings_payload(...) → set cfg.settings_path
  cwd derivation (263-267)              project maestro → ProjectStore.for_maestro
                                        root as cwd; PA stays cwd=None

runtime/claude_adapter.py
  ClaudeAdapterConfig (54-67)           + settings_path: Path | None
  _build_pty_extra_args (113-138)       append ["--settings", str(settings_path)]

ownership snapshot writer              on registry change, write ~/.hive/ownership.json
  (owned roots + owners) so the guard stays live mid-session
  (else: bake the rule into spawn env — simpler, refresh-on-respawn)

tests/
  hooks/test_ownership_guard.py        stdin JSON → exit code, per policy shape
  process/test_ownership_policy.py     PA vs project-maestro writable sets
  process/test_lifecycle_settings.py   settings_path emitted + --settings flag
```

**Delivers acceptance #3–5.** Carries the deployed re-smoke (a real maestro,
bypass on, blocked from an owned root; reads + ownerless writes work).

## Call flow (Slice B, every spawn)

```
spawn ─▶ _adapter_config_from_entity(entity)
            │  policy = writable_policy(entity, project_store)
            │  write settings.json{ hooks.PreToolUse: settings_payload(policy) }
            │  cfg.settings_path = <file>;  cfg cwd = project root (proj maestro)
            ▼
        claude_adapter → _build_pty_extra_args → "--settings <file>"
            ▼
        CC runs (bypass on). On each Write/Edit/MultiEdit/NotebookEdit:
            PreToolUse → ownership_guard.py → exit 2 (block) | 0 (allow)
```

## Open implementation choices (carry into the issues)

- **Snapshot vs bake-at-spawn** for the guard's owned-roots source (design §2).
- **Bash best-effort:** whether the matcher also covers `Bash` to flag obvious
  `>`/`tee`/`cp`/`sed -i`. Default: not in v1 (file tools only).
- **Guard invocation:** `python3 -m hive.hooks.ownership_guard` vs an absolute
  path — must resolve on the VPS regardless of the Entity's cwd.
