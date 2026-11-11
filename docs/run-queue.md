# Run page

`/run` on the desk is a short list of fix buttons. Each button is one exact,
named action from a fixed list. No button takes text, and nothing you type
reaches a command. It is owner-only and tailnet-only, like every desk page.

## How a tap works

1. Tap **Run**. The card shows, in plain words, what it will do, the exact
   command (or admin helper verb) and its time-out. Nothing has run yet.
2. Tap again within a few minutes to run it. The buttons that take things down
   (Restart WSL and Restart the PC now) ask once more. The second time they say
   what goes down and how it comes back.
3. The result shows in plain words: **OK**, **Refused** (it did not run),
   **Failed**, **Timed out**, or **Unknown** (the desk restarted before it saw the
   end). The last 20 runs are listed beside the buttons, newest first.

Without the page script each step is its own page with a button. A link, a GET
request or another site's page cannot start a run. Every run needs the desk's
step-up token, and that token is bound to that action, that number and that
request id.

## Rules

- **Fixed list only.** The Linux buttons run the repo's own units and script, as
  your user inside WSL, never as root. The Windows buttons only write one request
  line for the [admin helper](../deploy/windows/README.md#the-admin-helper) and start its task.
  They use only verbs the helper already has.
- **One at a time.** Each Linux button runs alone. All Windows buttons share one
  lane, because the helper has one request file.
- **Rate limit.** The same button again after 60 seconds at the earliest, and at
  most 10 runs in any 10 minutes across all buttons. A refused tap is not a run.
- **Hard time-out.** Each button has its own time-out (below). Past it, the desk
  stops the command and says so. A Windows request the helper never took is
  withdrawn, so it cannot run later.
- **Logged.** Every run writes two lines to `run-audit.log` in the desk's data
  folder (`HIVE_GATEWAY_DATA_DIR`, mode 0600). The first is written when it starts.
  The second says who ran it, which action, when it started, the result, how long
  it took and the result in plain words. Links, tokens and e-mail addresses are
  redacted. A `gateway-audit action=run ...` line also goes to the journal.
- **Safe to retry.** Every button can be run again; none deletes data. A confirmed
  tap sent twice runs once.

## The buttons

| Button | Runs exactly | Usually takes (time-out) | If it fails |
|---|---|---|---|
| Restart the desk | `systemctl --user restart --no-block hive-gateway.service`. It is refused unless this desk is that service. | about 10 s; the page reloads itself (20 s) | systemd starts it again within seconds, and the fleet-up timer does within 5 minutes. Still gone: use the Telegram bot's `/health`. |
| Restart the clip desk | `systemctl --user restart clip-desk.service`, then `is-active` | about 10 s (45 s) | Tap **Re-run fleet-up**. Still failed: ask firstmate to read `journalctl --user -u clip-desk.service -n 30`. |
| Restart the Telegram bot | `systemctl --user restart hive-telegram.service`, then `is-active` | about 10 s (45 s) | "Not running (inactive)": the bot is not set up yet ([telegram.md](telegram.md)). Otherwise as for the clip desk. |
| Re-run fleet-up | `systemctl --user start hive-fleet-up.service` (the same run its 5-minute timer starts; never a second firstmate) | under a minute (4 min) | **Timed out:** fleet-up carries on by itself; check the Machine page in a few minutes. **Failed:** see the Fleet-up timer row on the Machine page. |
| Repair the serve maps | `scripts/fleet-up.sh --only serve` (`tailscale serve`, tailnet only, never funnel) | about 10 s (60 s) | The ports Tailscale did not take are named. Check the Tailscale row on the Machine page, tap **Tailscale up**, then try again. |
| Check the admin helper | helper verb `ping` (changes nothing) | about 5 s (30 s) | The helper is not set up or Windows will not start it: see the [sitting](anywhere-sitting.md), step 2. Until it answers, no other Windows button is offered. |
| Restart WSL | helper verb `restart-wsl` (asks twice) | 1 to 3 min until the desk is back (60 s) | All of Linux restarts, this desk too. The keep-alive task starts it again. Not back after 5 minutes: someone opens Ubuntu at the PC. |
| Restart Tailscale | helper verb `restart-tailscale` | about 15 s (60 s) | The desk drops off the tailnet for a few seconds; reload. Not back in a minute: someone at the PC. |
| Tailscale up | helper verb `tailscale-up` (`tailscale up --unattended`) | up to 60 s (90 s) | A new sign-in cannot be done from here. Sign in to Tailscale at the PC (Machine page, Tailscale row). |
| Restart the PC now | helper verb `restart-pc`: restart in 30 seconds (asks twice) | 5 to 12 min until the desk is back (30 s) | The PC signs in and locks by itself after the sitting. Not back after 15 minutes: someone has to go to the PC. |
| Pause Windows updates | helper verb `pause-updates <days>` (1, 3, 7, 14, 21 or 35; 7 by default) | about 5 s (30 s) | Running it again starts a new pause from now. Still failing: ask firstmate to read the helper's audit log. |

The time-out of a Windows button is how long the desk waits for the helper's
reply. A restart can finish after that, and the list shows its result once the
desk is back.

## Not offered

Both wait on one follow-up: a cancel verb in the admin helper. Changing the
helper was out of scope here.

- **Cancel a scheduled restart.** The admin helper has no cancel verb, so the
  desk cannot cancel a restart. At the PC, run `shutdown /a`.
- **Restart the PC at a set hour.** The helper's `restart-pc` takes an hour, but
  a restart set for later must not exist without a way to cancel it, so the desk
  only restarts the PC now. The button comes with the cancel verb.

The page shows both as notes, not buttons.

## When the admin helper is not set up

The Windows buttons show only when the helper is installed:
`C:\ProgramData\HiveAdmin\request\` and `schtasks.exe` are both there. Until
then the page says "The Windows admin helper is not set up yet; see the sitting
steps." and offers no Windows button. When the helper is installed but has not
answered yet (it has no audit log yet, or its last desk run got no answer),
only **Check the admin helper** is offered.

## Configuration

| Env | Default |
|---|---|
| `HIVE_GATEWAY_ADMIN_HELPER` | `/mnt/c/ProgramData/HiveAdmin` (the helper's request folder and audit log; `off` removes the Windows buttons) |
| `HIVE_GATEWAY_SCHTASKS` | `schtasks.exe` on `PATH`, else `/mnt/c/Windows/System32/schtasks.exe` (starts the helper's task, `/run /tn '\Hive\Hive Admin Helper'` only; `off` removes the Windows buttons) |
| `HIVE_GATEWAY_SYSTEMCTL` | `systemctl` on `PATH` (the Linux buttons; without it they show "Not on this host") |
| `HIVE_GATEWAY_FLEET_UP` | `scripts/fleet-up.sh` in this checkout (Repair the serve maps) |

## Tests

`tests/gateway/test_runqueue.py` uses fakes only: a fake `systemctl`, fleet-up
and `schtasks.exe` that plays the admin helper, using the helper's own allow-list
and request grammar parsed from `deploy/windows/HiveAdminHelper.ps1`. Nothing in
the tests runs against the real PC or the real helper.
