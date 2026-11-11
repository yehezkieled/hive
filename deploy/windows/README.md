# deploy/windows

Windows side of "the PC as a self-healing server". The captain's step list is
[`docs/anywhere-sitting.md`](../../docs/anywhere-sitting.md); this file is the
reference behind it.

| File | Purpose |
| --- | --- |
| `Install-HiveAnywhere.ps1` | The one-sitting script. Run once as administrator. Idempotent; `-WhatIf` previews. |
| `Register-HiveWsl.ps1` | The WSL keep-alive task. The installer calls it with `-LogonOnly`. Also usable alone (see `docs/fleet-up.md`). |
| `HiveAdminHelper.ps1` | The narrow admin helper. Copied by the installer to `C:\Program Files\Hive\AdminHelper\`. |
| `tests/` | PowerShell tests (no Pester) and the shared request cases. Run by `tests/test_windows_deploy.py` when `pwsh` is installed. |

## What the installer changes

Every step checks first and reports `changed`, `unchanged`, `would change`
(under `-WhatIf`), `skipped`, `manual` or `failed`. A failed step does not stop
the others. Exit code 1 if any step failed.

| Step | Change | Check that it is already right |
| --- | --- | --- |
| a. Tailscale unattended | `tailscale set --unattended=true`. The PC joins the tailnet before anyone signs in. | `tailscale debug prefs` has `ForceDaemon: true`. |
| b1. ARSO "Always" | `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`: `DisableAutomaticRestartSignOn=0`, `AutomaticRestartSignOnConfig=1`. Windows signs in and locks after update and planned restarts. | Both values already set. |
| b2. Autologon | Sysinternals `Autologon64.exe <user> <domain> <password> /accepteula`. Covers cold boots (power cut, crash). | `Winlogon\AutoAdminLogon=1` and `DefaultUserName` is this user. Re-run with `-ResetAutologon` after a password change. |
| b3. Lock at logon | Task `\Hive\Hive Lock At Logon`: at this user's logon, `rundll32 user32.dll,LockWorkStation`. | Same task already registered. |
| c1. WSL keep-alive | Task `Hive WSL Keepalive` via `Register-HiveWsl.ps1 -LogonOnly`: at logon, a hidden loop runs `wsl.exe -d Ubuntu -u hezki --exec /usr/bin/sleep infinity` and starts it again 10 s after it ends. | Same action, user and logon type. |
| c2. Retire Startup script | Moves `Startup\wsl-keepalive.vbs` to `%LOCALAPPDATA%\Hive\retired-startup\`, only after `Hive WSL Keepalive` is registered and enabled. The `sleep` it already started is left running. | No file in Startup. |
| d. `.wslconfig` | `%UserProfile%\.wslconfig`: `[wsl2] vmIdleTimeout=-1`, `[general] instanceIdleTimeout=-1`. Other lines kept; old file saved as `.wslconfig.bak-hive`. Takes effect at the next WSL start. | Both keys already `-1`. |
| e. Admin helper | Folders and permissions below, the helper copied in, task `\Hive\Hive Admin Helper` (SYSTEM, on demand, no trigger), and the task's permissions set so this user may start it. | File hash, task and permissions already match. |
| f. Checklist | Prints BIOS power-on, Tailscale key expiry, Windows Hello-only and Claude sign-in as `manual`. | - |

`.wslconfig`: support for these keys in WSL 2.7.3 is unverified. WSL ignores
keys it does not know, so the lines are harmless if ignored. The keep-alive
loop is what actually holds WSL up.

### The password

- Asked for only when Autologon is not set yet, or with `-ResetAutologon`.
  Never under `-WhatIf`.
- Read with `Read-Host -AsSecureString`, typed twice to catch typos.
- Handed to `Autologon64.exe` as its command-line argument through
  `System.Diagnostics.Process`, so PowerShell transcripts and module logging
  never see it. Autologon stores it as an LSA secret.
- `Autologon64.exe` must carry a valid Microsoft Corporation signature or it is
  not run. If none is found next to the script or in
  `C:\Program Files\Hive\Sysinternals\`, it is downloaded from
  `https://live.sysinternals.com/Autologon64.exe` and then signature-checked.
- If a plain-text `Winlogon\DefaultPassword` exists afterwards, it is deleted.
- Accepted exposure (the captain's choice): an administrator on the PC can read
  the stored password back. BitLocker is off, so anyone with the disk could
  already read it. The house is the security boundary.
- While Autologon runs, the password is on its command line. Only processes
  allowed to read an elevated process's memory (administrators) can see it
  during that second. `[UNSURE]` whether Autologon in this mode shows a
  dialog; it is expected to exit silently.

## The admin helper

One elevated task, started on demand, that runs one verb from a fixed list.
Nothing calls it yet; the desk's Run queue (a later piece) will.

### Verbs

| Verb | Number | Does |
| --- | --- | --- |
| `ping` | - | Nothing. Logs `pong`. For testing that the task can be started. |
| `restart-wsl` | - | Restarts the `WSLService` (or `LxssManager`) service. All WSL distros stop; the keep-alive loop starts Ubuntu again. |
| `restart-tailscale` | - | Restarts the `Tailscale` service. |
| `tailscale-up` | - | `tailscale up --unattended --timeout=60s`. Output (for example a login link) goes into the audit log. |
| `restart-pc` | optional hour `0`-`23` | `shutdown /g`: restart in 30 s, or at the next HH:00 local time. With ARSO it signs in and locks again. |
| `pause-updates` | optional days `1`-`35` (default 7) | Sets the Windows Update pause values under `HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings`. |

Whether an unelevated process from WSL can start the on-demand helper task
without an admin prompt is unverified; `ping` is the only no-side-effect way
to prove it during the sitting, and it changes nothing on the machine beyond
appending an audit-log line.

### How to call it

1. Write one line to `C:\ProgramData\HiveAdmin\request\request.txt`
   (`/mnt/c/ProgramData/HiveAdmin/request/request.txt` from WSL):
   `<nonce> <verb> [<number>]`, ASCII, at most 128 bytes, optional line end.
   The nonce is 8 to 64 of `A-Z a-z 0-9 - _`, starting with a letter or digit,
   and new for each request.
2. `schtasks.exe /run /tn '\Hive\Hive Admin Helper'`
3. Read `C:\ProgramData\HiveAdmin\audit.log` for lines with that nonce:
   `started`, then `ok` or `failed`. A request that does not validate is logged
   as `rejected` with a reason. A nonce seen last time is `ignored`.

### Why it cannot run anything else

| Layer | How |
| --- | --- |
| No input but one short line | The task has fixed arguments. The only input is the request file, read as bytes, at most 128, printable ASCII only. |
| Strict grammar | One regex anchored at both ends, ASCII digits only. The verb must equal an allow-list key exactly (case-sensitive). The number exists only for two verbs and is range-checked as an `[int]`. |
| Fixed handlers | Each verb is one branch of a `switch` with hard-coded commands. Request text is never run, never passed to a shell, and never pasted into a command line. |
| No PATH lookups | `shutdown.exe` and `tailscale.exe` come from `System32` and `Program Files` paths taken from Windows, not from `PATH`. Module path is reset to the system one. |
| Code is admin-only | The helper lives in `C:\Program Files\Hive\AdminHelper\`: SYSTEM and Administrators full control, Users read. Nothing running as the user can change what SYSTEM runs. |
| Runs as SYSTEM | Not as the user, so the user's environment variables and profile do not reach the elevated process. |
| Task is start-only | The task's permissions: SYSTEM and Administrators full; this user read and run only, so the user cannot change its action. |
| Read-only on user ground | The helper only reads the request folder. It never writes or deletes there, and refuses if the folder or file is a link. Its state (`state\last-nonce`, admin-only) and the audit log are in admin-owned folders. |
| Audit | Every start appends one line, including rejected and ignored ones. Rejection reasons never echo the request text. |

**Plainly: anything that runs as the captain's Windows user, or as any agent
in WSL (WSL processes run as that user on the Windows side), can trigger every
verb on this list.** That is why the list is short and every verb is
something the captain would be fine with an agent doing unasked: each one at
worst restarts something or delays updates. No verb reads files, changes
settings beyond the update pause, or takes free text. A general "run anything
as admin" helper cannot be made safe on this PC and is deliberately not built.

The helper limits what the helper does. It does not make the captain's
Windows account a security boundary against code running as that account:
Microsoft does not treat UAC as one.

### Folders

| Path | Who can write |
| --- | --- |
| `C:\Program Files\Hive\AdminHelper\HiveAdminHelper.ps1` | SYSTEM, Administrators |
| `C:\ProgramData\HiveAdmin\audit.log` | SYSTEM, Administrators (Users read) |
| `C:\ProgramData\HiveAdmin\request\` | SYSTEM, Administrators, the captain's account |
| `C:\ProgramData\HiveAdmin\state\` | SYSTEM, Administrators (nobody else reads) |

## Not verified (needs the real PC)

| # | Unknown | How the sitting finds out |
| --- | --- | --- |
| 1 | Whether an unelevated process (from WSL) may start the SYSTEM task with `schtasks /run` using the task permissions set here, with no UAC prompt. Folder `\Hive` permissions may also matter. | Step 2 of the sitting (`ping`). |
| 2 | `AutomaticRestartSignOnConfig=1` as the "Always" mode of ARSO. | The test restart lands on the lock screen. |
| 3 | Autologon with a Microsoft account and Windows Hello-only sign-in on. | Same. |
| 4 | A brief unlocked moment between auto sign-in and the lock task. | Watch the screen once. |
| 5 | `vmIdleTimeout` / `instanceIdleTimeout` in WSL 2.7.3. | Harmless either way. |
| 6 | Service name `WSLService` for `restart-wsl` (store WSL); `LxssManager` is the fallback. | First real `restart-wsl`. |
| 7 | `tailscale up --unattended` run as SYSTEM, and whether it needs other flags repeated. | First real `tailscale-up`; output is in the audit log. |
| 8 | Whether Windows Update honours the pause values without the Settings app. | Settings > Windows Update shows "Updates paused". |
| 9 | BIOS menu name for power-on after AC loss. | The sitting, step 4. |

None of the scripts has been run on Windows. On Linux the request validation,
restart-hour and pause-window maths, argument quoting and `.wslconfig` merge
are tested through PowerShell, and every script is parsed.

## Undo

In an administrator PowerShell:

```powershell
# a. Tailscale unattended off
& "$env:ProgramFiles\Tailscale\tailscale.exe" set --unattended=false
# b. Auto sign-in off (Autologon GUI: Disable), ARSO policy back to unset, lock task
& "$env:ProgramFiles\Hive\Sysinternals\Autologon64.exe"
Remove-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name DisableAutomaticRestartSignOn, AutomaticRestartSignOnConfig
Unregister-ScheduledTask -TaskPath '\Hive\' -TaskName 'Hive Lock At Logon' -Confirm:$false
# c. Keep-alive task off, Startup script back
.\Register-HiveWsl.ps1 -Uninstall
Move-Item "$env:LOCALAPPDATA\Hive\retired-startup\wsl-keepalive.vbs" ([Environment]::GetFolderPath('Startup'))
# d. .wslconfig back
Move-Item "$env:USERPROFILE\.wslconfig.bak-hive" "$env:USERPROFILE\.wslconfig" -Force
# e. Admin helper gone
Unregister-ScheduledTask -TaskPath '\Hive\' -TaskName 'Hive Admin Helper' -Confirm:$false
Remove-Item "$env:ProgramFiles\Hive\AdminHelper", "$env:ProgramData\HiveAdmin" -Recurse -Force
```

If there was no `.wslconfig` before, delete it instead of moving the backup.
