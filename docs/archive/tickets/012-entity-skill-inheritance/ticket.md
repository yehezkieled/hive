# 012 — Curate & expose Claude Code skills to Entities per role

## What

Entities already inherit the user's `~/.claude/skills` library (Hive
spawns `claude` on the default config dir — no isolation, unlike the
Lona/Wonder bots). This ticket makes that deliberate and safe: select
the skills appropriate for each Entity role, exclude the ones that
would misbehave autonomously, and prompt the role JDs
(`role-maestro.md`, `role-lead.md`, `role-worker.md`) to actually use
the relevant ones.

## Why

Running native Claude Code via PTY means the entire skill ecosystem is
already available to Entities — the highest-leverage capability Hive
gets "for free" from its runtime choice. Used deliberately, a Worker
can run `tdd` / `diagnose`, a Maestro can use planning skills, and so
on. But inheriting *all* skills is dangerous:

- **interactive** skills (`grill-me`, `prototype`) hang waiting for a
  human;
- **self-recursive** skills (`build-with-agent-team`, `plan-next-sprint`)
  let an Entity spawn its own teams / sprints — uncontrolled fan-out;
- **sensitive** skills (`telegram:access`, `update-config`) must not be
  reachable.

"Most, not all" is the requirement.

## Acceptance

- A documented, per-role curation list of allowed skills, plus an
  explicit exclude-list covering interactive / self-recursive /
  sensitive skills.
- Each Entity role reaches only its curated subset, enforced by a
  per-role **deny**-list — Claude Code has no allowlist, so curation is
  exclusion (see [ADR 0008](../../adr/0008-per-role-skill-curation-denylist.md)).
  Verified under the PTY harness.
- The role JDs prompt Entities to use the relevant skills.
- No human-interactive or self-recursive skill is reachable by an
  Entity — verified.
- A smoke check shows an Entity successfully invoking an allowed skill.

## Note

Feature ticket (not a refactor) — needs a design pass on the curation
list and the load/allow mechanism under PTY. `design.md` / `outline.md`
/ `plan.md` authored in Phase B.
