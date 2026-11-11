# A standalone fixed-command Telegram bot for when Tailscale is down

## Status

Accepted, 2026-11-11. Builds on [ADR 0033](0033-hive-cutover-retire-entity-runtime.md)
(Telegram is an optional backup channel) and
[ADR 0034](0034-automatic-firstmate-recovery-ladder.md) (the watchdog switch).

## Context

The owner wants to reach firstmate with nobody at the PC, including when
Tailscale is down. They picked the Claude app plus the Telegram bot as the second
way in and accepted that this leaves the tailnet, outbound only, with fixed
commands only (wake, restart, status, health, watchdog).

`hive-telegram.service` ran `python -m hive`: the whole Vault runtime. That needs
PostgreSQL and the entity machinery, and it exposes every typed command
(`/m:<entity>`, `/kill`, `/approve`, `/vault`, file upload). On the owner's PC
there is no database, so the unit would crash-loop, and the command surface is
far wider than the owner asked for.

## Decision

`hive-telegram.service` runs `python -m hive.telegram` (`telegram/remote.py`): a
bot with no database and no Hive runtime, only the gateway's settings.

- Six fixed commands: `/status`, `/health`, `/wake`, `/restart`, `/watchdog
  on|off|status`, `/help`. No command takes free text.
- Only allowlisted ids in a private chat get a reply. Anyone else gets nothing,
  and only their id is logged.
- Outbound long polling only. No webhook, no listening port.
- Wake, Restart and the watchdog switch are the same code as the Vault runtime's
  bridge (`telegram/recovery.py`), so both share the desk's guards.
- The token lives in `~/.config/hive/telegram.env` (0600), written by
  `scripts/telegram-setup.sh`. The wrapper reads it, never sources it, and skips
  a malformed, loose or Telegram-rejected config without printing it. The bot
  redacts the token from every log line.

The Vault runtime keeps its own Telegram bridge. Both poll one token, so they
never run together (`Conflicts=hive.service`, plus the wrapper's process check).

## Consequences

- The bot stays up when the database, the Vault and the desk are down.
- While the bot owns the token, the Vault's typed Telegram commands are not
  available; the legacy web app keeps those approvals, and they are due to go
  anyway (#293).
- Telegram can read these messages. Kill switches: end the Telegram session,
  `/revoke` in BotFather, or `telegram-setup.sh remove`.
