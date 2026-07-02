# 063 — Dogfood kickoff + smoke (the finance app on Hive)

> **Backlog — re-grill when scheduled into a sprint.** Roadmap Phase 6.
> The on-ramp; depends on isolation (055–057) + create-flow (058).

## What

Stand up the finance-app project on Hive and prove **maestro → lead → leaf
end-to-end on the external repo**: a project maestro homed in the finance-app
repo spawns a lead whose worktree is *in that repo*, a leaf edits a file there,
the ownership guard allows it, and Hive's own checkout stays clean. Then kick off
the real build.

## Why

The deployed acceptance that isolation + the per-project worktree floor +
create-flow actually let a real product build run on Hive — the multi-repo
analogue of Ticket 023's live definition of done. This build is the forcing
function that generates the rest of the hardening backlog.

## Acceptance

TBD at grilling.
