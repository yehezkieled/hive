---
id: E04
title: Project isolation
milestone: M2
status: todo
issue: 287
---
## Goal
A project built on Hive cannot touch Hive's files, database, env, or ports: leads build in the project's own repo and its subprocesses see only the project's resources.

## Tickets
- T008 todo P1 Project isolation design (sandbox model)
- T009 todo P1 Per-project worktree floor
- T010 todo P1 Project isolation: own DB, env, ports

## Flow
<!-- drawn by pm flow, do not edit by hand -->
T008 -> T009
T008 -> T010
Ready now: none
To grill: T008
Blocked: T009 (waits on T008), T010 (waits on T008)

