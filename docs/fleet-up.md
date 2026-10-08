# Fleet up: boot auto-start and prevention

After an unattended restart nothing came back for 9 hours. This is the fix:
the OS service manager brings up the Hive desk and keeps it up, and one
idempotent script brings up everything else and, if it is not running,
firstmate.

Files (all shipped in this repo, none installed by it):

| File | Purpose |
| --- | --- |
| `scripts/fleet-up.sh` | The idempotent "fleet up" script. `--dry-run` prints and changes nothing. |
| `deploy/fleet-up/fleet-up.conf` | Paths and ports (the captain's current values). Override in `~/.config/hive/fleet-up.conf` or the environment. |
| `deploy/systemd/hive-gateway.service` | Gateway, `Restart=always`. |
| `deploy/systemd/hive-fleet-up.{service,timer}` | Runs fleet-up at boot and every 5 minutes. |
| `deploy/windows/Register-HiveWsl.ps1` | Task Scheduler task that starts and holds WSL. |
| `deploy/systemd/hive-telegram.service` | Hive runtime with Telegram (`python -m hive`), `Restart=always`, only if configured; `Conflicts=hive.service`. |
| `scripts/hive-telegram.sh` | Wrapper: loads the env file, skips cleanly when token/allowlist are missing or another Hive runtime runs. |
| `deploy/macos/com.hive.telegram.plist` | LaunchAgent for the Hive runtime with Telegram, `KeepAlive` on crash only. |
| `deploy/macos/com.hive.gateway.plist` | LaunchAgent, `RunAtLoad` + `KeepAlive`. |
| `deploy/macos/com.hive.fleet-up.plist` | LaunchAgent, `RunAtLoad` + every 5 minutes, `AbandonProcessGroup` so started services outlive it. |

## What fleet-up does

In order: Hive gateway (:8480, via the service manager), the Hive runtime with
Telegram (via its service), broke-no-more (:3001), the Lavish bridge
(`node lavish-proxy.js`, :4388), the preview server (:8765), then
`tailscale serve --bg --https=8443..8446` to the local ports (tailnet only,
never `funnel`), then firstmate. Step names for `--only`: `gateway runtime bnm
lavish preview serve firstmate`.

Each service step probes its port and only starts what is closed. The
firstmate step exits without doing anything when either a herdr pane in
`FM_DIR` runs the `claude` agent or a `claude` process has `FM_DIR` as its
working directory, so it never starts a second firstmate. Otherwise it makes
sure the herdr server is up and runs `claude` in a pane of an earlier
`firstmate` workspace whose `claude` has exited (for example no network at
boot), so each rerun retries the same session; only when there is no such
workspace does it create one at `FM_DIR`. It runs `claude` (the launch command in firstmate's README, "Install and
launch"). [UNSURE] Starting the herdr server with `herdr server` when it is
down was not exercised here; if it needs a different command, set it by
bringing herdr up first and the step skips the start.

Check without changing anything:

```sh
scripts/fleet-up.sh --dry-run
scripts/fleet-up.sh --dry-run --only serve,firstmate
```

Logs of started services: `~/.local/state/hive-fleet-up/` (macOS the same).

## Hive runtime with Telegram

`hive-telegram` runs `python -m hive`: the full Hive runtime (Vault rail,
quota and health monitors, the legacy web app when `HIVE_WEB_PORT` is set) with
Telegram as its optional backup channel (ADR 0033). It is not a bot-only
process. It **replaces `hive.service`**, which runs the same thing: never run
both, or two runtimes share one database and two pollers fight over one bot
token (Telegram answers `409 Conflict`). The unit has `Conflicts=hive.service`,
and the wrapper skips with a log line (`hive-telegram: skipping: a Hive runtime
is already running ...`) whenever any `python -m hive` process already runs, so
fleet-up, launchd and systemd never start a second one.

It is its own unit: no dependency on the gateway in either direction, so a
runtime crash or missing config never stops the desk.

Config is never committed. Create `~/.config/hive/telegram.env` (mode 0600):

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Without both values the unit is skipped (systemd `ExecCondition`) or exits 0
(launchd, not restarted) and logs `hive-telegram: skipping: ...`.
`python -m hive` also reads the rest of Hive's configuration (database, see
`docs/DEPLOYMENT.md`) from the same environment; add those lines to the env
file if they are not already provided. [UNSURE] Whether the bot needs more
than the Postgres DSN beyond the token and allowlist was not tested here.

## Windows (WSL) install

Prerequisites, once, inside WSL:

```sh
# /etc/wsl.conf must contain:
#   [boot]
#   systemd=true
# then, from PowerShell: wsl --shutdown
sudo loginctl enable-linger "$USER"

# hive-telegram.service replaces hive.service: stop and disable it first.
systemctl --user disable --now hive.service 2>/dev/null || true

mkdir -p ~/.config/systemd/user ~/.config/hive
cp ~/apps/hive/deploy/systemd/hive-* ~/.config/systemd/user/
cp ~/apps/hive/deploy/fleet-up/fleet-up.conf ~/.config/hive/fleet-up.conf
# On WSL Tailscale is the Windows one:
#   TAILSCALE_BIN='/mnt/c/Program Files/Tailscale/tailscale.exe'
systemctl --user daemon-reload
systemctl --user enable --now hive-gateway.service hive-telegram.service hive-fleet-up.timer
```

Then, in an elevated PowerShell on Windows:

```powershell
.\deploy\windows\Register-HiveWsl.ps1 -DryRun     # preview
.\deploy\windows\Register-HiveWsl.ps1             # register
```

The task runs `wsl.exe -d Ubuntu -u hezki --exec /usr/bin/sleep infinity`
hidden, at startup and at logon, and restarts if it ends. Holding a process
keeps the WSL VM (and so the systemd user services) alive. Use `-Distro` and
`-User` if yours differ. [UNSURE] An S4U task runs without a stored password
but only while the machine is up; confirm after the first reboot with
`wsl -l -v` and `systemctl --user status hive-gateway`.

### Windows uninstall

```powershell
.\deploy\windows\Register-HiveWsl.ps1 -Uninstall
```

```sh
systemctl --user disable --now hive-fleet-up.timer hive-gateway.service hive-telegram.service
rm ~/.config/systemd/user/hive-{gateway.service,telegram.service,fleet-up.service,fleet-up.timer}
systemctl --user daemon-reload
```

## macOS install

fleet-up runs natively (bash 3.2 compatible). Needs `jq`, `node`, `herdr` and
Tailscale (`TAILSCALE_BIN=/Applications/Tailscale.app/Contents/MacOS/Tailscale`
when the CLI is not on PATH).

```sh
mkdir -p ~/.config/hive ~/Library/LaunchAgents
cp deploy/fleet-up/fleet-up.conf ~/.config/hive/fleet-up.conf
# in that file, set the gateway start for launchd:
#   GATEWAY_START_CMD='launchctl kickstart gui/$(id -u)/com.hive.gateway'
#   RUNTIME_START_CMD='launchctl kickstart gui/$(id -u)/com.hive.telegram'
#   RUNTIME_STATUS_CMD='launchctl print gui/$(id -u)/com.hive.telegram | grep -q "pid = "'
# Stop any other Hive runtime (python -m hive) first; the job skips while one runs.
for f in com.hive.gateway com.hive.telegram com.hive.fleet-up; do
  sed -e "s#__HIVE_DIR__#$HOME/apps/hive#g" -e "s#__HOME__#$HOME#g" \
    deploy/macos/$f.plist > ~/Library/LaunchAgents/$f.plist
  plutil -lint ~/Library/LaunchAgents/$f.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$f.plist
done
```

LaunchAgents start at user login, so enable automatic login (System Settings
> Users & Groups) or the Mac sits at the login window after a restart.

### macOS uninstall

```sh
for f in com.hive.gateway com.hive.telegram com.hive.fleet-up; do
  launchctl bootout gui/$(id -u)/$f
  rm ~/Library/LaunchAgents/$f.plist
done
```

## Prevention guide

Goal: the machine does not sleep, and restarts happen when nobody is relying
on it.

### Windows

- **No sleep on power**: Settings > System > Power > Screen and sleep > set
  "When plugged in, put my device to sleep after" to Never. Or
  `powercfg /change standby-timeout-ac 0` and
  `powercfg /change hibernate-timeout-ac 0`. Disable hybrid sleep and
  hibernation (`powercfg /h off`) if it should never suspend.
- **Restarts outside active hours**: Settings > Windows Update > Advanced
  options > Active hours. Set them to cover the hours the fleet must be up
  (a window of up to 18 hours). Windows will not auto-restart for updates
  inside it. Also turn on "Notify me when a restart is required to finish
  updating", and consider Group Policy "No auto-restart with logged on users
  for scheduled automatic updates installations".
- **WSL idle shutdown**: `Register-HiveWsl.ps1` keeps a process attached; also
  set `vmIdleTimeout=-1` under `[wsl2]` in `%UserProfile%\.wslconfig` on WSL
  builds that support it. [UNSURE] Key availability depends on the WSL version.
- **Wake-on-LAN**: possible only for waking a sleeping or powered-off PC from
  another device on the same LAN, not over the tailnet by itself (magic
  packets are broadcast on the LAN; a second always-on device on the LAN, such
  as a router or Pi, is needed to send one for you). Enable it in BIOS/UEFI
  ("Wake on LAN" / "Power on by PCI-E") and in Device Manager > the Ethernet
  adapter > Power Management > "Allow this device to wake the computer" and
  "Only allow a magic packet". Wi-Fi adapters usually cannot wake from full
  power-off. It does not help while the PC is up but WSL is down. Fast Startup
  can break WoL from shutdown; turn it off. [UNSURE] Per-NIC support varies.

### macOS

- **No sleep**: `sudo pmset -a sleep 0 disksleep 0` and, on a laptop on power,
  `sudo pmset -c sleep 0`; `sudo pmset -a autorestart 1` powers the Mac back on
  after a power failure. Check with `pmset -g`. For a bounded run,
  `caffeinate -dimsu -t 3600` holds sleep off for an hour; as a standing guard
  `caffeinate -s &` while on AC power.
- **Restarts outside active hours**: System Settings > General > Software
  Update > (i) > turn off "Install macOS updates" automatic installation, or
  leave downloads on and install by hand in the evening. macOS has no active
  hours setting; automatic installs restart overnight. `sudo pmset repeat
  wakeorpoweron MTWRFSU 03:55:00` can schedule a wake before a planned
  window.
- **Wake-on-LAN**: Apple calls it "Wake for network access" (System Settings >
  Battery/Energy > Options). `sudo pmset -a womp 1`. It works on Ethernet
  for a sleeping Mac, and on recent Macs on Wi-Fi only from sleep, not from
  power-off. Same LAN limitation as Windows: the magic packet must originate
  on the LAN. [UNSURE] Wi-Fi behaviour varies by model.
