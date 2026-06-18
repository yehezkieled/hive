# 033 — Research

Code-grounded findings. Verified directly (parallel exploration + a
completeness critic, then hand-checked the load-bearing claims). Line numbers
are as of the ticket-033 branch off `main`.

## The live prompt-assembly path

```
spawn maestro
  └─ _ownership_spawn_overrides(entity)                  lifecycle_manager.py:298–322
        is_pa = entity.name == DEFAULT_MAESTRO           lifecycle_manager.py:311
        └─ _maestro_fence → settings_path                (write-fence ONLY; Ticket 024)
  └─ _adapter_config_from_entity(entity)                 lifecycle_manager.py:101–125
        → ClaudeAdapterConfig            (no is_pa field) claude_adapter.py:54–71
  └─ _get_or_create_adapter: config.settings_path = …    lifecycle_manager.py:324–368
        └─ ClaudeAdapter.start() → _build_pty_system_prompts()   claude_adapter.py:96–114
              cfg.system_prompt
              "You are {name}. Your role is {role}."
              loop text (LOOP_PROMPTS[cfg.loop_mode])
              if role in (maestro, lead): load_role_jd(cfg.role) ⇒ role-maestro.md
```

**Core gap (confirmed):** `is_pa` is computed only for the write-fence and never
flows to prompt assembly. Both the PA and project maestros receive the same
`role-maestro.md`, which assumes project ownership — so the PA mis-identifies.
PA-ness is *enforced* (Ticket 024 / ADR 0017) but never *communicated*.

## Key file references

| What | Location |
|------|----------|
| Live prompt builder (the seam to fix) | `src/hive/runtime/claude_adapter.py:96–114` (`_build_pty_system_prompts`) |
| Adapter config dataclass (add `is_pa`) | `src/hive/runtime/claude_adapter.py:54–71` (`ClaudeAdapterConfig`) |
| Entity→config mapping (set `is_pa`) | `src/hive/process/lifecycle_manager.py:101–125` (`_adapter_config_from_entity`) |
| Sole `is_pa` computation (consolidate) | `src/hive/process/lifecycle_manager.py:311` |
| Role-JD loader (unchanged) | `src/hive/process/loops.py:15–30` (`load_role_jd`, `_VALID_ROLES`) |
| Loop-prompt constants (add block beside) | `src/hive/process/loops.py:33` (`LOOP_PROMPTS`) |
| PA identity constant | `DEFAULT_MAESTRO`, `src/hive/config.py:98` |
| Maestro model (add `is_pa` property) | `src/hive/models/maestro.py:12` |
| Mis-describing JD | `personalities/role-maestro.md:3–5` (ownership) and `:7` (stale "Workers") |

## Two corrections to the ticket's premise

1. **`entity.build_cli_args` is dead code.** Defined at `entity.py:290` (and
   `Maestro.build_cli_args`, `maestro.py:37`), it builds the headless
   `claude -p … --output-format stream-json` invocation — the non-PTY runtime
   **removed in Ticket 007**. Call sites: tests only (`test_entity.py`,
   `test_process_manager.py`, `test_vault.py`); **no production caller**. The
   ticket's acceptance "keep the duplicate logic consistent" is therefore
   over-specified — keeping dead code in sync has no runtime effect.

2. **`role-maestro.md:7` is also stale:** "Workers do the actual coding."
   Workers were retired (ADR 0013) and the entity deleted (Ticket 018). Leaf
   work runs as ephemeral Leaf agents inside a Lead's Workflow run. Same file
   033 already edits → fixed narrowly here.

## Test landscape

- `_build_pty_system_prompts` has **no direct test** — `tests/runtime/test_claude_adapter.py`
  covers only `_build_pty_extra_args`. 033 adds the first.
- `Maestro` has no dedicated test file; Maestro unit tests live in `tests/test_entity.py`.
- `_adapter_config_from_entity` is exercised by `test_adapter_config_maps_entity_fields`
  in `tests/process/test_lifecycle_manager.py` — extend it for `is_pa`.
- `is_pa`'s write-fence behaviour is already well-tested
  (`tests/process/test_ownership_policy.py`, `tests/process/test_ownership_integration.py`) —
  unchanged by this ticket (identity only, no policy change).

## Scattered PA references (mapped, NOT refactored — non-goal)

`config.py:98` (`DEFAULT_MAESTRO`), `lifecycle_manager.py:311` (computation),
`ownership_policy.py:30–38` (`writable_policy`), `project_store.py:55–67`
(`owned_roots` — "the PA's write-fence list"), `permissions.py:131–156` +
`message_dispatcher.py:625` (`can_kill` protects the PA), `__main__.py`
(routing defaults), `telegram/help_text.py:98` (string literal),
`web/view_model.py:179–191` (`_OTTER_STUB`), `migrations/025_rename_pa_to_otter.sql`.
033 adds the **one** source of truth these could later consolidate onto
(`Maestro.is_pa`) but does not rewrite the consumers.
