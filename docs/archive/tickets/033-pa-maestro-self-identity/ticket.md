# 033 — PA Maestro self-identity: tell the PA it's the PA

> Found during the S7-planning live check: otter introduced itself as a generic
> "your maestro on Hive". Codebase research confirmed the cause — `is_pa` exists
> only for the Ownership-guard write-fence (Ticket 024, ADR 0017) and never
> reaches the maestro's prompt.

## What

Make a maestro's prompt state its **structural role** — PA Maestro vs. project
maestro — derived from `is_pa` (`entity.name == DEFAULT_MAESTRO`), so the PA
knows it is the default route, owns no project, and may read any project but
write only ownerless ones. Today PA-ness is *enforced* (the write-fence) but
never *communicated*, so the PA reads a generic project-maestro JD and
mis-identifies.

## Why

- Prompt assembly (`entity.py:build_cli_args` and the live, PTY-only
  `claude_adapter.py:_build_pty_system_prompts`) injects identity + role JD but
  never checks `is_pa`; `load_role_jd` is a fixed role→file map
  (`maestro` → `role-maestro.md`) with no PA variant.
- `role-maestro.md` assumes a project owner ("decide what teams the project
  needs", "a maestro owns a project") — false for the PA.
- PA-ness is scattered (`config`, `lifecycle_manager`, `ownership_policy`,
  `project_store`, `web/view_model`, `help_text`, migration `025`) with no single
  source of truth and no link to the prompt.
- With 024's PA write-policy live, a PA that *thinks* it's a project maestro can
  reason wrongly about ownership — a correctness gap, not just cosmetics.

## Acceptance

- A maestro's system prompt states whether it is the PA or a project maestro,
  keyed on `is_pa`, covering: default-route + owns-no-project +
  read-any/write-ownerless (PA) vs. owns-one-project (project maestro).
- The **live** path (`claude_adapter._build_pty_system_prompts`) carries it; the
  duplicate logic in `entity.build_cli_args` stays consistent (resolve the
  duplication in `design.md`).
- `role-maestro.md`'s project-owner framing no longer mis-describes the PA.
- otter, re-deployed, introduces itself as the PA (owns no project); project
  maestros are unaffected.
- Tests cover PA vs. project-maestro prompt assembly.

## Non-goals

- Centralising every scattered PA reference (note them; don't refactor all).
- Changing the write-fence behaviour (Ticket 024 / ADR 0017) — this is identity
  only.
- New PA capabilities or policy.

## Notes

Design fork for `design.md` (Phase B / Grill #2): a distinct `role-pa.md` JD vs.
an `is_pa`-keyed append-system-prompt block vs. a personality-file flag; and
whether to unify the two prompt-assembly seams. Likely small-to-medium,
direct-lane; possibly ADR-worthy if the role-JD strategy is hard to reverse.
