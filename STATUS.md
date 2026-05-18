# Hive — Status

_Last verified: 2026-05-18_

A one-screen view of where Hive is right now. Build history lives in
`docs/PROJECT_PLAN.md`; what's planned next lives in `ROADMAP.md`. This
file is just the current state.

## Top priority

**Sprint 30 — PTY harness migration must go live before 2026-06-15.**
On that date Anthropic moves headless `claude -p` to API billing; the
PTY path keeps Hive plan-billed. The code is written — see "Done but
NOT live" below — but it is not yet deployed.

## Live in production

Sprints 1–29. The `hive.service` systemd unit has run since 2026-05-10.
The daily `pg_dump` backup timer (Sprint 29 Phase 1) is active.

## Done but NOT live

- **Sprint 30 — PTY harness migration.** Committed to local `main`,
  which is 9 commits ahead of `origin/main`. Not pushed, not deployed
  (the running service predates the Sprint 30 code), and `HIVE_USE_PTY`
  is not set in `.env` so the PTY path is off. Going live means: push,
  restart the service, verify, then flip `HIVE_USE_PTY=true`.

## On hold / deferred

- **Sprint 29 Phase 2 — DigitalOcean droplet snapshot.** Token-gated:
  needs a DigitalOcean Personal Access Token in `.env` as `DO_API_TOKEN`
  and `DO_DROPLET_ID`. Revisit once the token is provisioned.
- **Sprint 21 Phase 2+ — restart persistence.** Only Phase 1 shipped.

## Roadmap — not started

Detail in `ROADMAP.md`.

- Phase 2 — restructure: break up `manager.py`, consolidate the Vault,
  rename `WorkerAgent` → `Worker`.
- Phase 3 — web dashboard → installable PWA.
- Phase 4 — Codex and OpenCode adapters.
- Phase 5 — features (to be defined).

## Branch hygiene

Hive squash-merges pull requests. A squash merge creates a new commit
with no parent link to the feature branch, so `git branch --no-merged`
reports fully-merged branches as unmerged — **do not trust it**. Delete
each feature branch right after its PR merges. As of 2026-05-18 the only
branch is `main`.

## Keeping this file current

Update this file whenever project state changes — at minimum as part of
the post-sprint ritual, alongside `docs/PROJECT_PLAN.md` and
`docs/DEPLOYMENT.md`. It is deliberately short so the update is cheap.
If it goes stale anyway, add a CI check that compares it against `git`
and `PROJECT_PLAN.md` and fails the build on drift.
