# Plan — Ticket 024: Project ownership & PA write-policy

1 project ↔ ≤1 Maestro, enforced by a per-spawn `PreToolUse` ownership-guard
hook that fences file-edit tools to an Entity's writable set **under bypass
mode** (ADR 0016). Fan-out: 2 vertical slices, B depends on A.

## Slices

| Summary (what the slice delivers) | Issue | Blocked by |
|-----------------------------------|-------|------------|
| Project registry + ownership (1↔≤1 maestro, `/project` cmds) | [#150](https://github.com/yehezkieled/hive/issues/150) | — |
| Ownership-guard PreToolUse hook + project-home cwd (bypass-proof) | [#151](https://github.com/yehezkieled/hive/issues/151) | #150 |

Full spec (acceptance + file paths) lives in each issue — not here.

## Execution waves

- **Wave 1:** #150  (registry — self-contained postgres CRUD)
- **Wave 2:** #151  (the fence — reads #150's registry)

## Conventions

- Branch `ticket-024/issue-<n>-<slug>`, target `main`, squash-merge.
- Validation gate (every PR): `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest -m "not integration"`.
- **#151 also needs a deployed re-smoke** (real maestro, bypass on, blocked from
  an owned root; reads + ownerless writes work) before close — S6's
  "behaviour, not deletion" rule.
- Autonomy: #150 is AFK (auto-merge on green CI + passing review). #151 holds
  for the deployed re-smoke, then merges.

## Cross-cutting impact (already landed in this ticket's docs commits)

- ADR 0016 (new) — the hook-under-bypass decision + probe evidence.
- `CONTEXT.md` — glossary: **Project**, **Project ownership**, **Ownership
  guard**; "project" ambiguity flag.
- No `README`/`DEPLOYMENT` change (guard ships in `src/`, no new service/port).

> To build: run the fleet Workflow against this `plan.md`.
