# Automatic firstmate recovery: a guarded watchdog ladder in the gateway

## Status

Accepted, 2026-10-12. Builds on [ADR 0030](0030-firstmate-implemented-in-hive.md)
(Hive is the front door of firstmate) and the manual Wake / Restart session
controls.

## Context

Wake and Restart session exist, but only a person can tap them, and a person who
is away cannot reach firstmate when it falls silent. The owner asked Hive to act
as the backup captain and, asked whether it should restart firstmate by itself,
said yes. It is security-sensitive: Hive stops and starts a session unattended.

## Decision

The gateway runs a watchdog (`gateway/watchdog.py`) that climbs a fixed ladder,
only while work is under way: Wake after T1 of silence if firstmate is dead, one
Restart session after T2 more, then stop and alert after T3 or after 2 automatic
restarts in 6 hours. It is **off by default**; the owner switches it on from the
desk or an allowlisted Telegram chat, and the same switch is the kill switch.

It reuses `WakeService.wake` / `restart` and so fleet-up's firstmate step, the
mid-turn refusal and the audit log; it holds no start logic and no firstmate
rules. Firstmate's own settings are read-only through `fmconfig`; missing or
unparsable settings, or an unusable Hive watchdog config, mean do nothing and
alert. Thresholds live in Hive's `watchdog.json`. Ladder state is persisted and
claimed before an action runs, so a gateway restart cannot repeat a step. A
manual Wake or Restart resets the ladder.

## Consequences

- A silent firstmate recovers without a human, at most twice in 6 hours, and
  never while it is mid-turn.
- A stuck loop is bounded: it stops, alerts, and waits for the owner.
- The watchdog can lose a session's unsaved work (the same cost as a manual
  Restart); the mid-turn refusal and the cap limit it.
- Telegram announcements go through the bot process (which owns the token) from
  a shared event file, so they need `hive-telegram` running; the desk and Web
  Push do not.
