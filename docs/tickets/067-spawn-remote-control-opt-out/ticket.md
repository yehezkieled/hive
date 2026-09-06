# 067 — Hive spawns must not auto-connect to Remote Control

> Un-sprinted live-annoyance fix, found on 2026-09-06: every Hive restart
> produced three new Remote Control sessions in the Claude app (one per
> maestro), each later retitled "Hive scheduler eval" by the 120-min poke.
> Trivial ticket — `ticket.md` only.

## What

Every Hive spawn — maestro **and** lead — now carries an explicit
`remoteControlAtStartup: false` in the per-spawn `--settings` file that
`LifecycleManager._get_or_create_adapter` already injects.

- New `_BASE_SPAWN_SETTINGS` + `_spawn_settings_payload()` in
  `process/lifecycle_manager.py`: the role-independent base settings, merged
  with the Ticket 024 ownership-guard hook when a maestro is fenced.
- `_ownership_spawn_overrides` now returns the ownership **payload** (dict or
  None) instead of a written path; the spawn path writes one merged file for
  every entity. Leads and a PA that owns nothing previously got no `--settings`
  flag at all; now they get the base settings.

Nothing else changes: no `--remote-control` flag is ever passed, the
ownership fence is byte-identical inside the merged file.

## Why

**Root cause is Claude Code's startup resolver, not Hive passing a flag.** Hive
never passes `--remote-control`. The CLI (2.1.263, and the 2.1.261 the live
maestros ran) resolves Remote Control auto-start as:

```
explicit remoteControlAtStartup setting  →  org policy  →  GrowthBook rollout default
```

With the key unset on this host, the rollout default is currently **on**, so
every flag-less interactive session — Hive's PTY-driven maestros included —
opened a bridge (`bridgeSessionId` present in `~/.claude/sessions/<pid>.json`).
The `--settings` file tier is honoured for this key, and an explicit `false`
short-circuits the fallback (`false ?? default` keeps `false`), while an
explicit `--remote-control` flag still wins — so the user's own `spawn-agent`
sessions, which pass the flag, are unaffected.

**Reproduced end-to-end on this host** before the change: two throwaway
interactive `claude` sessions, one with `--settings {}` and one with
`--settings {"remoteControlAtStartup": false}`. The first got a
`bridgeSessionId`; the second did not.

**Why in Hive's spawn settings, not the user's `~/.claude/settings.json`.** A
user-level `false` would also work today, but it makes Hive's correct
behaviour depend on host config that lives outside the repo. Hive owns the
sessions it spawns; the opt-out belongs to the spawn.

## Acceptance

- `_spawn_settings_payload(None) == {"remoteControlAtStartup": False}`; with an
  ownership fence, the hook is kept alongside the opt-out.
- `_get_or_create_adapter` passes a `--settings` file containing
  `remoteControlAtStartup: false` for a maestro **and** a lead (previously a
  lead got no settings file).
- Ownership-integration tests updated to the payload contract; ruff + full
  `pytest -m "not integration"` green.
- After `systemctl --user restart hive.service`, the respawned maestros'
  `~/.claude/sessions/<pid>.json` records carry **no** `bridgeSessionId`.
