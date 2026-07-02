# 058 — Create-project + maestro flow (git-validated)

> **Backlog — re-grill when scheduled into a sprint.** Roadmap Phase 6.

## What

One guided, atomic flow to **register a project** at a validated git repo path +
**create/assign its (non-PA) maestro** together, with sane unattended defaults.
Collapses today's manual, never-linked `/new maestro` + `/project new|assign`.

## Why

Today creating a project maestro is a two-step CLI dance with no validation (that
the path is a git work-tree, that the maestro isn't the PA) — a high chance of a
mis-wired project (maestro homed with no git; leads silently using Hive's repo).

## Acceptance

TBD at grilling. (May surface on the web once the redesign lands — S10 only
*displays* projects; creation is here.)
