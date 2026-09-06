# 024 — Project ownership & PA write-policy

## What

Introduce a minimal project-ownership model and enforce the PA
Maestro's write boundary:

1. **Project registry** — projects exist as first-class records
   (name → root path → owning Maestro, nullable).
2. **1 project ↔ max 1 Maestro** — assigning a second Maestro to an
   owned project is rejected. Project Maestros are ad-hoc: spun up
   when a project needs one.
3. **PA write-policy** — the PA Maestro (default route, currently
   `otter`) may *read* any project, but may *edit* only projects with
   **no** responsible Maestro. Owned projects are read-only to the PA.
4. **Project home** — a project Maestro's session runs in its
   project's root (today every Maestro sits in the live Hive checkout
   purely because `cwd=None` falls through to the service
   WorkingDirectory).

Enforcement follows 015's policy-in-code pattern (`tool_policy.py`):
generate per-path CC permission rules (deny `Edit`/`Write` under owned
project roots) at **every** spawn, restart-proof — never prompt-only.

## Why

From the 023 design grill (2026-06-11), where the Maestro
relationships were sharpened into `CONTEXT.md`: one PA Maestro as the
default route, project Maestros owning exactly one project each,
Maestro↔Maestro limited to shared-resource coordination. Without an
ownership model, nothing stops the PA from editing a project behind
its Maestro's back — the org chart says "CEO per project" but the
filesystem says "everyone writes everywhere."

## Acceptance

- Project registry exists; create/assign rejects a second Maestro on
  an owned project.
- PA Maestro in an owned project: file edits denied (per-path
  permission rules visible in the spawn config); reads work.
- PA Maestro in an ownerless project: edits work.
- A project Maestro spawns with its project root as cwd.
- Policy applied on every spawn (restart included); hermetic tests at
  the policy seam.
- `ruff` + `pytest -m "not integration"` green.

## Non-goals

- The worktree floor / session pinning (023).
- Lead/Worker write-policy (the floor already scopes leaf writes;
  revisit only if leaf work needs cross-project access).
- Multi-user ownership, project sharing, or quotas per project.

## Notes

Opened from the 023 grill. S6 candidate; depends on 023's floor being
live (the floor is what makes "where an entity writes" controllable).
Cross-links: `CONTEXT.md` Relationships (PA Maestro, 1-project-1-
maestro norm), Ticket 023 (floor), ADR 0008/0010 (policy-in-code
pattern).
