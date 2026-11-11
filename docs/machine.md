# Machine page

`/machine` on the desk shows, in one screen, the chain that has to hold for the
iPad to reach firstmate, and the dated risks coming up. It is read-only: it has
no buttons and adds no endpoint that writes. The firstmate chip in the header
is the same one every desk page carries.

Each row has a state chip with a word (OK, Warning, Down or Unknown), a short
headline, one line of detail and when it was last checked. A row is **Unknown**
when its check did not answer, timed out, is switched off or returned something
not understood. Unknown never means fine. A red band marks a date inside 3
days.

When Tailscale on the PC is down the desk cannot be reached at all. Use the
Telegram bot's `/health` then ([telegram.md](telegram.md)).

## The chain

| Row | What it shows | When it is not OK |
|---|---|---|
| PC power | How long Windows has been up, and when it started. | Unknown only: the Windows read failed. See "Unknown rows" below. |
| Windows session | Signed in, Locked (signed in with the screen locked; both are OK) or Nobody signed in. | **Nobody signed in:** sign in at the PC. Tailscale, WSL and the desk start with that sign-in. |
| Tailscale | Whether the PC's Tailscale is connected, and when its key expires. | **Key expired / Logged out:** log in to Tailscale at the PC. **Turned off:** turn it on at the PC. **Waiting for approval:** approve the PC in Tailscale's admin page. **Running, offline:** check the PC's internet. **Starting:** wait a minute. A key inside 14 days is a Warning: renew it, or turn key expiry off for the PC, in Tailscale's admin page. |
| WSL | How long the Linux side has been up. | Unknown only. A WSL that is down cannot serve this page. |
| Desk | How long this desk has been up, and whether it runs as its service. | **Not installed as a service / its service is failed:** the desk was started by hand and will not come back after a restart. Reinstall the service ([fleet-up.md](fleet-up.md#windows-wsl-install)). |
| Fleet-up timer | Whether the timer that restarts firstmate and the desk is on, and its last run. | **Not installed / Off:** install or enable it ([fleet-up.md](fleet-up.md)). **Last run failed:** read `journalctl --user -u hive-fleet-up.service -n 30` on the PC. **Overdue / No run yet:** it should run every 5 minutes; check the timer on the PC. |
| Firstmate | Whether firstmate's session is alive, its last activity, and whether automatic recovery (the watchdog) is on. | **Down:** tap the firstmate chip, then Wake firstmate. **Watcher silent:** tap the chip, then Restart session. **Watchdog halted or its config unusable:** see [fleet-up.md](fleet-up.md#automatic-recovery-watchdog-ladder). |
| Claude sign-in | Days left on the PC's Claude sign-in, and the date it ends. | **Warning (inside 14 days) or Expired:** run `/login` in a Claude terminal at the PC, open the link and paste the code back. Once it expires every agent stops, firstmate included. |
| Internet | One outbound connection from the PC (it sends nothing). | **Not reachable:** check the router or the provider. Agents cannot reach Claude until it is back. |

## Upcoming risks

Sorted nearest first.

| Risk | What it means | What to do |
|---|---|---|
| Windows restart | **Now:** Windows has a restart pending and will take it outside active hours. **Within days:** this month's updates are out (second Tuesday) and not installed yet. Otherwise the date of the next monthly updates. | Expect the PC to restart. Afterwards someone has to sign in at the PC, unless automatic sign-in is set up. |
| Tailscale key, this PC | The date the PC's key expires. After it the desk is unreachable. | Renew the key, or turn key expiry off for the PC, in Tailscale's admin page. |
| Tailscale key, *device* | The same for your other devices on the tailnet, such as the iPad. | Open Tailscale on that device and sign in again before the date. |
| Claude sign-in | The date the PC's Claude sign-in ends. | `/login` at the PC before the date. |

## Unknown rows

| Detail | Cause |
|---|---|
| PowerShell was not found | The desk is not on Windows (WSL), or `HIVE_GATEWAY_POWERSHELL` points nowhere. PC power, Windows session and Windows restart stay Unknown on any other host. |
| Tailscale's command was not found | Set `HIVE_TAILSCALE_BIN`. |
| The systemctl command was not found | No systemd on this host: Desk shows "Service state unknown" and Fleet-up timer is Unknown. |
| Its record was not found | The Claude credentials file is missing (`HIVE_GATEWAY_CLAUDE_CREDENTIALS`), or `/proc/uptime` is on a non-Linux host. |
| The check did not answer in time | The command took longer than 2.5 s and was stopped. It is tried again on the next round. |
| This check is switched off | Its setting is `off`. |
| Checking | The last readings are over 2 minutes old; a new round is running. |

## How it reads

Every check is an unelevated, read-only command or file read with a time-out
under 3 seconds: `powershell.exe` (boot time, which sessions hold a desktop or
a lock screen, the two restart-pending registry keys, the newest installed
update), `tailscale status --json`, `systemctl --user show`, `/proc/uptime`,
the expiry date in Claude Code's credentials file, and one TCP connect. They
run off the request: the page answers from the last readings and starts a new
round behind it when they are over 15 seconds old (boot time and installed
updates at most every 10 minutes). Apart from one round when the desk starts,
nothing runs while nobody has the page open. An open page refreshes itself
when a round finishes and asks again every 30 seconds.

From the credentials file only the sign-in's expiry date is read. From
Tailscale only the state, the key dates and device names of your own account
are kept. Anything token-like, an e-mail address or a link is replaced by
`[redacted]` before it is shown.

Settings are in [DEPLOYMENT.md](DEPLOYMENT.md#gateway-desk-adr-0030).
