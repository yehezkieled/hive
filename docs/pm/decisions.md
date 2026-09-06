# Decisions

One entry per decision, newest first. A decision is anything a future reader
would otherwise have to guess: a library choice, a data shape, a rule you set.
When a decision changes, add a new entry and write "Replaces entry of <date>"
so the old entry stays as history.

Architecture decisions keep living in `docs/adr/` (append-only, numbered).
This file holds the smaller process and tooling decisions the pm workflow makes.

## 2026-09-06: Mirror the board to GitHub issues
Context: Hive already files GitHub issues per ticket by hand (#254, #264). The pm plugin can mirror milestones, epics, and tickets one way.
Decision: `mirror: on`. `docs/pm` markdown is the source of truth; `pm.py sync` creates and updates the issues.
Consequences: Issue numbers land in each ticket's `issue:` field. Editing an issue on GitHub does not flow back.

## 2026-09-06: Git flow stays branch + PR
Context: Every change so far has landed through a PR into `main`, with CI running ruff and pytest.
Decision: `flow: branch-pr`, `merge: ask`. /pm:work branches per ticket and opens a PR; merging is confirmed by hand.
Consequences: Deployment (push, restart `hive.service`, journal check, Tailscale smoke) stays the post-merge step from CLAUDE.md.

## 2026-09-06: Adopt the pm plugin layout
Context: Planning lived in a three-altitude layout (roadmap / sprints / ticket folders with six artifacts, ADR 0003). Sprint files were calendar windows and the six-artifact workflow was never automated.
Decision: Replace it with the pm plugin's milestone → epic → ticket → subtask layout under `docs/pm/`. Milestones are ordered versions, not dates. Old planning docs move to `docs/archive/`; ADRs stay in `docs/adr/`. Recorded as ADR 0028, superseding ADR 0003.
Consequences: One place for live planning. Tickets are single files with What / Why / Acceptance / Subtasks / Plan; grilling replaces the questions → research → design → outline chain. Open tickets 047, 051–065 were migrated; done tickets are history only.
