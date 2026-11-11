# Telegram bot: the way in when Tailscale is down

A small bot on the PC that answers a few fixed commands from your phone. It
works when Tailscale is down, as long as WSL is up. See
[ADR 0035](adr/0035-standalone-telegram-bot.md).

> **Security note.** This path leaves the tailnet. Your messages go through
> Telegram's cloud, and Telegram can read them. The PC only makes outbound
> HTTPS calls to Telegram (polling). No port is opened on the PC.

## What it can do

Only these commands exist. Nothing you type is run as a command.

| Command | What it does |
| --- | --- |
| `/status` | Firstmate's state in one line. |
| `/health` | The whole chain in one message: desk up, firstmate session up, last firstmate activity, automatic recovery on or off, Tailscale on the PC. |
| `/wake` | Firstmate's state, with a **Wake firstmate** button when it is down, or **Restart session** when its watcher is silent. |
| `/restart` | Restart a stuck firstmate session. Asks you to confirm first. |
| `/watchdog on`, `/watchdog off`, `/watchdog status` | Automatic recovery. `on` asks you to confirm; `off` is immediate. |
| `/help` | The command list. |

Anything else (plain text, files, other commands) gets the command list back.

Rules:

1. Only your Telegram user id is answered, and only in a private chat with the bot.
2. Anyone else gets no reply. The bot logs their user id, never their text
   (at most once an hour per id).
3. Wake and Restart share the desk's guards: one action per 60 seconds, never a
   second firstmate, and an audit line for each try (see
   [fleet-up.md](fleet-up.md#wake-firstmate-desk-and-telegram)).
4. A Restart or `/watchdog on` confirm works once, for 2 minutes, on that message.
5. The token never appears in a log line or a reply.

## Set it up (once, about 5 minutes)

### 1. Create the bot with BotFather

1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`.
3. Give it a name, then a username ending in `bot`.
4. BotFather replies with a token like `123456789:AA...`. Keep it secret.

### 2. Find your Telegram user id

1. In Telegram, open a chat with **@userinfobot**.
2. Send any message. It replies with your numeric **Id**.

Without a third-party bot: do step 3 with any id (for example `1`), send your
bot a message, then read your id from
`journalctl --user -u hive-telegram.service | grep allowlist` and run
`telegram-setup.sh set --user-id <your id>`.

### 3. Save the token on the PC

In a WSL terminal:

```sh
~/apps/hive/scripts/telegram-setup.sh set
```

1. Paste the token. It is not shown while you type.
2. Type your user id and press Enter.

The helper writes `~/.config/hive/telegram.env` (mode 600, outside git) and
restarts the bot. If the service is not installed yet, it says so; fleet-up
starts the bot within 5 minutes once it is.

### 4. Check it

Open your bot in Telegram, press **Start**, then send `/health`.

1. From the allowed account, send `/status`, `/health` and `/wake`. Expect the
   firstmate state, the health chain lines, and a Wake or Restart button when
   one applies.
2. From a different Telegram account, send `/status` and plain text. Expect no
   reply. Then check `journalctl --user -u hive-telegram.service`; it should
   show only that user's id, with no message text.
3. From the allowed account, send plain text and `/restart please restart`.
   Expect the command list and the no-extra-text reply respectively, with
   nothing started.

## Rotate, remove, check

| Task | Command |
| --- | --- |
| Rotate the token | Revoke it in BotFather (`/revoke`), then `telegram-setup.sh set` and paste the new one. Press Enter to keep your user id. |
| Change the allowed user id | `telegram-setup.sh set --user-id <id>` (comma separated for more than one) |
| Turn the bot off | `telegram-setup.sh remove` (deletes the file and stops the bot) |
| See what is set | `telegram-setup.sh status` (never shows the token) |

## How it stays up

| Situation | What happens |
| --- | --- |
| No config | The service is skipped (not failed). fleet-up logs one line per run. |
| Config malformed, or readable by others | Same: skipped with a reason, the value is never printed. |
| Telegram rejects the token | The bot exits and is not restarted. Run `telegram-setup.sh set` with a new token. |
| No internet at boot | The bot retries by itself (5 s, doubling, up to 5 min). |
| The bot crashes | systemd restarts it after 30 s. |
| PC or WSL restart | The unit is enabled at boot, and fleet-up's timer starts it within 5 minutes if needed. |
| The Vault runtime (`python -m hive`) runs | The bot stays off, so one token never has two pollers. |

## Install the service

Part of the fleet-up install ([fleet-up.md](fleet-up.md#windows-wsl-install)).
After deploying a new Hive version, copy the unit again:

```sh
cp ~/apps/hive/deploy/systemd/hive-telegram.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable hive-telegram.service
```

Logs: `journalctl --user -u hive-telegram.service -n 30`.

The bot reads the same `HIVE_GATEWAY_*` settings as the desk (port, data dir,
firstmate home). If the desk unit overrides any, add the same lines to this
unit. `HIVE_TAILSCALE_BIN` picks the Tailscale command for `/health`; the desk's
Machine page reads the same setting. Its defaults and `off` option are documented
in [DEPLOYMENT.md](DEPLOYMENT.md#gateway-desk-adr-0030).

## Kill switches

| From | Do |
| --- | --- |
| Telegram | Settings > Devices: end the session of a lost phone. |
| BotFather | `/revoke` the token. The bot stops working at once. |
| The PC | `telegram-setup.sh remove`. |
