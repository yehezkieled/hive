# Decisions

One entry per decision, newest first. A decision is anything a future reader
would otherwise have to guess: a library choice, a data shape, a rule you set.
When a decision changes, add a new entry and write "Replaces entry of <date>"
so the old entry stays as history.

Architecture decisions keep living in `docs/adr/` (append-only, numbered).
This file holds the smaller process and tooling decisions.

## 2026-10-05: Design mockups live in `docs/design/`; the brainstorm scratch is gone (T005)
Context: T005 asked to remove `src/hive/web/static/brainstorm/` and `.superpowers/`, record where the redesign mockups live, and prune merged `ticket-*` branches.
Decision: Approved mockups live in `docs/design/` (reviewed in Lavish Editor, Tailscale link only). Neither scratch directory is tracked or present in the tree, so nothing was left to delete; `.superpowers/` stays in `.gitignore`. No `ticket-*` branches exist: the remaining local branches are `fm/hive-*` crew branches, several still checked out in other worktrees, so none were pruned. The brief named `docs/pm/decisions.md`; that file moved to this one on 2026-10-01.
Consequences: no behaviour change. Old `fm/hive-*` branches that are merged can be pruned by the captain or firstmate once their worktrees are released.

## 2026-10-03: Firstmate contract test runs the pinned scripts, not a vendored copy
Context: the desk assumes ticket ownership, handoff, roll-up and decision routing behave as firstmate documents, and a project second mate for Hive is planned (ADR 0030).
Decision: A separate CI job fetches one pinned commit of the captain's firstmate fork (`tests/firstmate_contract/FIRSTMATE_PIN`, shallow fetch) and drives its real scripts against throwaway primary and `hive` second-mate homes. It skips without `HIVE_FIRSTMATE_ROOT`, so the normal check is unchanged. Recorded in `docs/firstmate-contract-test.md`.
Consequences: a firstmate change that breaks the desk fails CI; bumping the pin is a deliberate commit. Two desk gaps it found are strict xfails until fixed.

## 2026-10-03: Desk live updates and push live in the gateway, not the Entity runtime
Context: ADR 0026's Web Push channel hangs off Hive's `NotificationDispatcher` and Postgres store, which retire at the cut-over (ADR 0030), and the desk reads firstmate's files, not Hive events.
Decision: The gateway runs its own watcher (`state/*.status` stat plus `fm-inbox.sh receipts`, snapshot on change) that feeds SSE and a file-backed push store (`HIVE_GATEWAY_DATA_DIR`, VAPID key and subscriptions mode 0600, outside the repo). It reuses ADR 0026's actionable-set idea and the `pywebpush` dependency, but nothing from the Entity runtime. The only new POSTs store or remove the owner's push subscription; no new action on firstmate.
Consequences: nothing to migrate at the cut-over; two push senders exist until then, one per service.

## 2026-10-01: Harness pivot — Pi first, headless default, PTY fallback
Context: Claude Code drops its login about monthly, which stops the whole fleet; headless runs currently share the subscription limits and a separate headless credit was announced then paused.
Decision: See [ADR 0029](adr/0029-harness-pivot-headless-default-pty-fallback.md) for the original pivot; harness availability and ordering are superseded by [ADR 0031](adr/0031-codex-headless-harness.md). Current configuration is owned by the [deployment runbook](DEPLOYMENT.md#harness-selection-adr-0029).
Consequences: Pi entities lose MCP, `/goal` and the Ownership guard (see ADR 0029 consequences); the fleet survives a Claude logout when Pi is signed in. Replaces nothing in this file; narrows ADR 0007.

## 2026-10-01: Work tracking leaves the repo
Context: the in-repo pm board (`docs/pm/tickets`, `epics`) was written by an older pm-plugin version that newer versions no longer read.
Decision: Open tickets moved to the maintainer's external backlog; each still links its GitHub issue. The repo carries no tickets or epics. `docs/pm/roadmap.md` and `decisions.md` moved to `docs/roadmap.md` and `docs/decisions.md`. Replaces entries of 2026-09-06 and the `mirror: on` entry.

## 2026-09-09: Command surface v2 (T007) design calls
Context: T007 (from 064) bundles four command changes plus a fifth — removing typed `/approve` `/deny` `/vault` — that is hard-gated on T004's needs-you buttons. Grilling settled the open forks.
Decision:
- Split the typed-approve/deny/vault removal into **T017** (`depends_on: T004`); T007 keeps the four independent changes.
- **`/goal` seeding:** Hive injects `/goal <completion condition>` into an entity at spawn, the deterministic path the role JD and loop prompt already use. The `loop_mode`/`LOOP_PROMPTS` injection is retired in its favour (not kept alongside native `/goal`). Build refinement (found at review): `send_to_entity` is a shared chokepoint — scheduler pokes, peer mail, and compact reseeds all flow through it — so "first turn" alone is the wrong trigger (it would wrap a poke or a compact continuation as the entity's goal). Seeding is gated on an explicit `seed_goal` flag that only the genuine user/command task entrypoint (`dispatch._send_to_entity`, which both Telegram and the web message/decision channel funnel through, plus the local CLI) sets; every internal machine send leaves it `False`.
- **`/model` billing warning:** driven by a named set of API-billed model names kept in one place; selecting a member warns, others are silent. `fable` is added as a valid `/model` name but is not assumed API-billed — Fable 5.1 runs under the user's Max plan today. The real API-billed names are verified in plan mode; if none qualify the set ships empty and the warning path is covered by a unit test.
- **`/mode` default:** a lead spawns `yotree` only when its project root is a git repo, else `yolo`, because yotree needs a git worktree. Maestros spawn `yolo`. One source of truth, no `lifecycle_manager` force-set.
Consequences: T007 stays one PR of four changes; T017 waits on T004. The billing-warning set is empty until a genuinely API-billed model exists, but the mechanism and its test ship now, so adding a name later is a one-line change.

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
