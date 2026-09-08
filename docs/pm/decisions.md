# Decisions

One entry per decision, newest first. A decision is anything a future reader
would otherwise have to guess: a library choice, a data shape, a rule you set.
When a decision changes, add a new entry and write "Replaces entry of <date>"
so the old entry stays as history.

Architecture decisions keep living in `docs/adr/` (append-only, numbered).
This file holds the smaller process and tooling decisions the pm workflow makes.

## 2026-09-08: Design tickets use the lavish-axi skill, mockups live in docs/design/
Context: T001–T003 were written for the external Claude design app, with the ticket closed by hand on approval. The lavish-axi skill (`~/.claude/skills/lavish`) opens an agent-authored HTML file in a browser where the reviewer annotates it and the feedback comes back to the session. Eight hand-coded brainstorm mockups already exist as HTML under `docs/archive/tickets/054-hive-cleanup/mockups/`.
Decision: T001–T003 are authored as HTML and reviewed in Lavish Editor instead of the Claude design app. Drafts sit in the gitignored `.lavish/` scratch; the approved file is exported to `docs/design/Txxx-slug.html`. Reviews are served over the Tailscale/MagicDNS link only; `lavish-axi share` (third-party host) is never used. The Claude design app stays available if a ticket needs it.
Consequences: Design tickets run through /pm:work like code tickets; their tdd gate is the browser review loop. `docs/design/` is the mockups' home, so T005 only has to decide what to do with the archived brainstorm files. Alternatives: the Claude design app (external, closed by hand, export by hand) and `docs/archive/.../mockups/` as the home (approved designs under archive/ mislead).

## 2026-09-08: `debate` stays embedded; other patterns are invoked as skills
Context: T006 grilling. The lead JD carries one inline `debate` recipe (Ticket 034, ADR 0020) while further coordination shapes ship as global skills (ADR 0021/0025). The mixed model set a "patterns = author them yourself" precedent, and a live Lead borrowed a shape name with 0 Skill calls.
Decision: Option (a) — keep `debate` embedded and say so explicitly in the JD; every other shape is a global skill the Lead invokes via the Skill tool. Rejected (b), converging `debate` into a skill, which would undo shipped ADR 0020 work and move a tested recipe outside the repo.
Consequences: Doc + test change only. ADRs 0020, 0021, 0025 all still stand. The Lead's free-form fallback is unchanged, so a missing skill degrades gracefully.

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
