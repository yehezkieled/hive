# Gone-dark alarm: your phone hears when the PC goes quiet

The PC cannot tell you it is off. So it does the opposite: while it is healthy it
calls a secret address at a hosted **dead-man's switch** every 5 minutes, and that
service alerts your phone when the calls stop.

Off by default. Nothing runs until you give it a ping address.

> **Security note.** This leaves the tailnet, outbound only. Each ping is one bare
> HTTPS GET to your address: no body, no PC name, no token, no machine data. The
> service learns only "this address was called at this time, from this IP". No port is
> opened on the PC. The address is a secret (anyone with it can send fake pings); it is
> never logged, printed or shown on the desk.

## How it works

`hive-gonedark.service` runs `python -m hive.gonedark`
([`src/hive/gonedark.py`](../src/hive/gonedark.py)). Every interval it checks, on the
PC itself, that the desk answers on loopback, that a firstmate session runs and that
Tailscale is signed in and connected (`tailscale status --json`, read only). Only then
does it ping. If any check fails it skips the ping and writes why to its log, so the
missing ping is the alarm. A Tailscale command that fails with no answer means its
daemon or service is not running, which counts as down. If Tailscale's answer cannot be
read (its command is missing, it does not answer in time, or it prints something
unreadable), that check counts as passed, so a probe glitch never raises a false alarm.

| Situation | Ping | What happens |
| --- | --- | --- |
| Desk up, firstmate session up, Tailscale connected | Sent | Nothing: all is well. |
| Desk not answering | Skipped, logged | Alert after period + grace. |
| No firstmate session | Skipped, logged | Alert after period + grace. |
| Tailscale signed out, stopped (`tailscale down`, or its daemon or service not running), or running but offline | Skipped, logged | Alert after period + grace. |
| Tailscale's answer cannot be read | Sent | No alert: a probe glitch is not an outage. |
| PC off, asleep, at the sign-in screen, or WSL stopped | Nothing runs | Alert after period + grace. |
| Home internet down | Failed, logged | Alert after period + grace. |
| Ping service slow or erroring | Failed (8 s time-out), logged | Tried again at the next tick, never sooner. |
| Sender crashed | None until systemd restarts it (30 s) | Usually no alert. |
| Not configured | The service is skipped | Machine page shows **Off**. |

Recovery is automatic: the first healthy tick pings again, and most services then send
a "back up" notice.

The Machine page shows one line for it under the chain: **Off**, **On** (last ping
sent), **Ping skipped** or **Last ping failed** with the fixed reason, or **On, not
running** when no tick has happened for two intervals. It shows the last ping's time,
never the address.

## Set it up (once, about 10 minutes)

### 1. Make a check at a dead-man's-switch service

Pick any service that alerts you when an https address stops being called. This is
your choice and your account; Hive works with any of them. One example is
Healthchecks.io (free tier for a few checks `[UNSURE]`; read its current terms):

1. Sign up, then **Add Check**.
2. Set **Period** to 5 minutes and **Grace** to 10 minutes.
3. Under integrations, add your phone (its app, email, Telegram, ntfy or similar).
4. Copy the check's ping address (`https://hc-ping.com/...`). Keep it secret.

Others that work the same way include Cronitor, Dead Man's Snitch and Better Stack
heartbeats `[UNSURE]`. Use the same period and grace. If you change the interval
below, set the period to match and the grace to about twice it.

### 2. Save the address on the PC

In a WSL terminal:

```sh
~/apps/hive/scripts/gonedark-setup.sh set
```

Paste the address. It is not shown while you type. The helper checks it is a plain
`https://` address, writes `~/.config/hive/gonedark.env` (mode 600, outside git) and
restarts the sender. If the service is not installed yet, it says so; fleet-up starts it
within 5 minutes once it is.

### 3. Check it end to end

1. `gonedark-setup.sh status` shows **On** and, within a minute, `Last tick: ..., sent`.
   The service's dashboard shows the check as up.
2. Stop the sender: `systemctl --user stop hive-gonedark.service`. Within period + grace
   (about 15 minutes) your phone gets the alert. (fleet-up starts it again within 5
   minutes, so for this test also run `systemctl --user stop hive-fleet-up.timer`.)
3. Start it again: `systemctl --user start hive-fleet-up.timer hive-gonedark.service`.
   Your phone gets the recovery notice at the next ping.

## Change, turn off, check

| Task | Command |
| --- | --- |
| Change the address | `gonedark-setup.sh set` and paste the new one. |
| Change the interval | `gonedark-setup.sh set --every 600` (60 to 3600 seconds; default 300). Match the service's period. |
| Turn it off | `gonedark-setup.sh remove` (deletes the file and stops the sender). Then pause or delete the check at the service, or it alerts. |
| See what is set | `gonedark-setup.sh status` (never shows the address). |
| Logs | `journalctl --user -u hive-gonedark.service -n 30` |

## Install the service

Part of the fleet-up install ([fleet-up.md](fleet-up.md#windows-wsl-install)). After
deploying a new Hive version, copy the unit again:

```sh
cp ~/apps/hive/deploy/systemd/hive-gonedark.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable hive-gonedark.service
```

It reads the same `HIVE_GATEWAY_PORT` and `HIVE_GATEWAY_FM_HOME` as the desk. If the
desk unit overrides either, add the same lines to this unit.
