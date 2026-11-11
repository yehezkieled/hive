# Anywhere: the one sitting at the PC

One visit to the PC so it comes back by itself after any restart. After this
the iPad reaches the desk and firstmate with nobody at the PC.

Time: about 30 minutes, plus a 15-minute test restart.
Who: the PC's owner, signed in as themselves. Nobody else runs this.

What the script does and why: [`deploy/windows/README.md`](../deploy/windows/README.md).

## Before you sit down (phone, 2 minutes)

1. Open the Tailscale admin console (login.tailscale.com) on the phone.
2. Machines > `desktop-lfme032` > the `...` menu > **Disable key expiry**.

The script cannot do this. Without it the PC drops off the tailnet on
20 Apr 2027.

## At the PC

### 1. Run the script

| # | Do this | You should see |
|---|---|---|
| 1 | Sign in to Windows. | Your desktop. |
| 2 | Start > type `PowerShell` > right-click **Windows PowerShell** > **Run as administrator** > **Yes**. | A blue window whose title starts with "Administrator". |
| 3 | `cd \\wsl.localhost\Ubuntu\home\hezki\apps\hive\deploy\windows` | The prompt ends in `deploy\windows>`. |
| 4 | `powershell -ExecutionPolicy Bypass -File .\Install-HiveAnywhere.ps1 -WhatIf` | A preview. Every line says `would change`, `unchanged` or `manual`. Nothing is changed. |
| 5 | `powershell -ExecutionPolicy Bypass -File .\Install-HiveAnywhere.ps1` | It asks for your Windows password, twice. |
| 6 | Type the password. For a Microsoft account use the account password, not the PIN. | Nothing shows while you type. That is normal. |
| 7 | Read the summary at the end. | `failed=0`. |

If a line says `failed`: take a photo of the summary, then run step 5 again.
The script is safe to run again. It only changes what is still wrong.

The password goes only to Microsoft's Autologon tool. The script never shows,
logs or saves it.

### 2. Check the admin helper (2 minutes)

Open **Ubuntu** from the Start menu (a normal window, not as administrator).
Run each check below. These confirm the helper can start, rejects malformed
requests, ignores a replay, and redacts links and tokens.

1. Check an allowed request (`ping`):

```sh
echo "check$(date +%s) ping" > /mnt/c/ProgramData/HiveAdmin/request/request.txt
schtasks.exe /run /tn '\Hive\Hive Admin Helper'
sleep 5; tail -n 3 /mnt/c/ProgramData/HiveAdmin/audit.log
```

2. Check a malformed request:

```sh
echo 'check$(date +%s) shell' > /mnt/c/ProgramData/HiveAdmin/request/request.txt
schtasks.exe /run /tn '\Hive\Hive Admin Helper'
sleep 5; tail -n 3 /mnt/c/ProgramData/HiveAdmin/audit.log
```

3. Check that a repeated nonce is ignored. Re-run the same request line and
task command twice; keep the nonce unchanged:

```sh
echo 'check-replay-001 ping' > /mnt/c/ProgramData/HiveAdmin/request/request.txt
schtasks.exe /run /tn '\Hive\Hive Admin Helper'
sleep 5; tail -n 1 /mnt/c/ProgramData/HiveAdmin/audit.log
test ! -e /mnt/c/ProgramData/HiveAdmin/request/request.txt && echo 'request removed'
echo 'check-replay-001 ping' > /mnt/c/ProgramData/HiveAdmin/request/request.txt
schtasks.exe /run /tn '\Hive\Hive Admin Helper'
sleep 5; tail -n 1 /mnt/c/ProgramData/HiveAdmin/audit.log
test ! -e /mnt/c/ProgramData/HiveAdmin/request/request.txt && echo 'request removed'
```

4. Check that authentication links and tokens are redacted:

```sh
echo "check$(date +%s) tailscale-up" > /mnt/c/ProgramData/HiveAdmin/request/request.txt
schtasks.exe /run /tn '\Hive\Hive Admin Helper'
sleep 5; grep -cE 'https?://' /mnt/c/ProgramData/HiveAdmin/audit.log
```

| You see | Meaning |
|---|---|
| `SUCCESS` and then a line with `result=ok detail=pong` | The helper works. Go on. |
| `result=rejected` for the malformed request | Request text was rejected; nothing ran. |
| `result=ok` then `result=ignored`, and `request removed` after each run | The nonce was consumed once and the request file was removed each time. |
| `0` from the URL check | No authentication links remain in the audit log; links and tokens are redacted. |
| `ERROR: Access is denied.` | The PC does not let a normal window start the helper. Note it for firstmate and go on. Nothing else depends on it yet. |
| No `pong` line after 30 seconds | Run the last line again. Still nothing: note it for firstmate and go on. |

### 3. Renew the Claude sign-in (5 minutes)

The Claude sign-in on this PC ends on **21 Nov 2026 at about 16:51 AEDT**.
After that every agent stops until someone renews it at the PC.

| # | Do this | You should see |
|---|---|---|
| 1 | In the Ubuntu window: `claude` | Claude Code starts. |
| 2 | Type `/login` and press Enter. Pick the Claude account (subscription) option. | A link. |
| 3 | Open the link (Ctrl+click), sign in, approve. | A code. |
| 4 | Copy the code, paste it into the Ubuntu window, press Enter. | "Login successful". |
| 5 | Type `/exit`. Then run `claude auth status`. | A login expiry date well after 21 Nov 2026. |

### 4. BIOS: power on after a power cut (5 minutes, optional but advised)

The script cannot change the BIOS. The menu names differ by maker, so look
around a little.

1. Start > Power > hold **Shift** and click **Restart**.
2. Troubleshoot > Advanced options > **UEFI Firmware Settings** > Restart.
3. Find a setting named like **Restore on AC Power Loss**, **AC Back**,
   **After Power Failure** or **Power On After Power Fail**. It is often under
   Power, Advanced or Chipset.
4. Set it to **Power On** (not "Last State", not "Off").
5. Save and exit (often F10).

The PC now boots. It should sign in and lock by itself. That is a first test.

### 5. The test restart, watched from the iPad (15 minutes)

1. At the PC, save your work and close windows.
2. Start > Power > **Restart**.
3. Walk away. From here on, only use the iPad.

What to look for on the iPad, in order:

| When (after you clicked Restart) | Where | What you should see |
|---|---|---|
| 1 to 3 min | Tailscale app > devices | `desktop-lfme032` goes offline, then **online again**. This happens before Windows signs in. |
| 2 to 5 min | (at the PC, if you glance back) | The lock screen, not the sign-in screen with an empty password box. |
| 3 to 8 min | `https://desktop-lfme032.tailfb3900.ts.net:8446/` | The desk loads. |
| 5 to 12 min | The desk | Firstmate shows as running. |

Wait the full 15 minutes before deciding it did not come back. The desk's
starter runs 30 seconds after WSL starts and then every 5 minutes.

### 6. If it does not come back

Find the first row that fits. You need to go back to the PC for most of them.

| What you see | Likely cause | What to do |
|---|---|---|
| PC never comes back online in Tailscale (15 min) | Windows stuck, or Tailscale not in unattended mode | At the PC: look at the screen. Sign in if needed. Re-run step 1.5 and check the Tailscale line. |
| At the PC: the normal sign-in screen, not locked | Auto sign-in did not work | Sign in. Turn off "only allow Windows Hello sign-in" (Settings > Accounts > Sign-in options). Re-run step 1.5 with `-ResetAutologon` at the end. |
| PC online in Tailscale, desk does not load (15 min) | WSL did not start | At the PC: open Ubuntu once. If the desk then loads, tell firstmate the keep-alive task did not start. |
| Desk loads, firstmate not running | Firstmate did not start | On the desk: **Wake firstmate**. Wait 2 minutes. Then **Restart firstmate**. |
| Desk loads, agents say "Login expired" | Claude sign-in | At the PC: step 3 again. |
| The PC was left unlocked after signing in | Lock task did not run | Press Win+L. Tell firstmate. |

To undo everything the script did, see "Undo" in
[`deploy/windows/README.md`](../deploy/windows/README.md#undo).
