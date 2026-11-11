<#
.SYNOPSIS
  Registers (or removes) the Task Scheduler task that keeps WSL - and so the
  Hive systemd user services - running.

.DESCRIPTION
  WSL's VM exits when no process is attached. The task runs a hidden loop that
  starts a long-lived `wsl.exe ... sleep infinity` and starts it again 10
  seconds after it ends (WSL crash, `wsl --shutdown`, WSL update). With
  systemd=true in /etc/wsl.conf and linger enabled, that brings up
  hive-gateway.service, clip-desk.service and the hive-fleet-up timer.

  Default: triggers at startup and at logon, S4U (no stored password).
  -LogonOnly: triggers at this user's logon only and runs in the signed-in
  session, the same way the old Startup-folder wsl-keepalive.vbs did. That is
  the mode Install-HiveAnywhere.ps1 uses, with auto sign-in.

  -DryRun prints what would be registered and changes nothing.
  Run from an elevated PowerShell. See docs/fleet-up.md and
  docs/anywhere-sitting.md.
#>
[CmdletBinding()]
param(
  [string]$Distro = 'Ubuntu',
  [string]$User = 'hezki',
  [string]$TaskName = 'Hive WSL Keepalive',
  [switch]$LogonOnly,
  [switch]$Uninstall,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# Both values are pasted into the task's command line, so allow plain names only.
if ($Distro -notmatch '^[A-Za-z0-9._-]{1,64}$') { throw "Distro '$Distro' must be letters, digits, '.', '_' or '-'" }
if ($User -notmatch '^[a-z_][a-z0-9_-]{0,31}$') { throw "User '$User' must be a plain Linux user name" }

if ($Uninstall) {
  if ($DryRun) { Write-Output "Would unregister scheduled task '$TaskName'"; return }
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Output "Unregistered '$TaskName'"
  return
}

$windowsUser = "$env:USERDOMAIN\$env:USERNAME"
$wsl = Join-Path ([Environment]::GetFolderPath('System')) 'wsl.exe'
$powershell = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
$loop = "while (`$true) { & '$wsl' -d $Distro -u $User --exec /usr/bin/sleep infinity; Start-Sleep -Seconds 10 }"
$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$loop`""
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments

if ($LogonOnly) {
  $triggers = @(New-ScheduledTaskTrigger -AtLogOn -User $windowsUser)
  $principal = New-ScheduledTaskPrincipal -UserId $windowsUser -LogonType Interactive -RunLevel Limited
  $triggerText = "at $windowsUser's logon"
  $logonText = 'Interactive (in the signed-in session)'
} else {
  $triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn)
  )
  # Run as the interactive user whether or not logged on needs a stored password;
  # a SYSTEM-owned task cannot see the user's distro, so use the user at logon
  # plus the startup trigger via S4U (no password stored).
  $principal = New-ScheduledTaskPrincipal -UserId $windowsUser -LogonType S4U -RunLevel Limited
  $triggerText = 'at startup, at logon'
  $logonText = 'S4U'
}
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -MultipleInstances IgnoreNew `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden

function Get-HiveAccountSid([string]$Account, [scriptblock]$Resolver = $null) {
  if ([string]::IsNullOrWhiteSpace($Account)) { return $null }
  if ($Account -ieq 'SYSTEM' -or $Account -ieq 'NT AUTHORITY\SYSTEM') { return 'S-1-5-18' }
  if ($Account -match '(?i)^S-\d-\d+(?:-\d+)+$') { return $Account.ToUpperInvariant() }
  try {
    if ($Resolver) {
      $resolved = & $Resolver $Account
    } else {
      $accountName = New-Object System.Security.Principal.NTAccount -ArgumentList $Account
      $resolved = $accountName.Translate([System.Security.Principal.SecurityIdentifier])
    }
    if ($resolved -is [System.Security.Principal.SecurityIdentifier]) { return $resolved.Value }
    $sid = [string]$resolved
    if ($sid -match '(?i)^S-\d-\d+(?:-\d+)+$') { return $sid.ToUpperInvariant() }
    return $null
  } catch {
    return $null
  }
}

function Test-HiveTaskAccountMatches([string]$Actual, [string]$Expected, [scriptblock]$Resolver = $null) {
  $actualSid = Get-HiveAccountSid -Account $Actual -Resolver $Resolver
  $expectedSid = Get-HiveAccountSid -Account $Expected -Resolver $Resolver
  return ($null -ne $actualSid) -and ($null -ne $expectedSid) -and
    [string]::Equals($actualSid, $expectedSid, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-HiveTriggerFingerprint($Triggers, [scriptblock]$Resolver = $null) {
  return @($Triggers | ForEach-Object {
    $class = $_.CimClass.CimClassName
    $user = if ($class -eq 'MSFT_TaskLogonTrigger') {
      Get-HiveAccountSid -Account ([string]$_.UserId) -Resolver $Resolver
    } else { '' }
    "$class|$user"
  }) -join ','
}

function Test-HiveKeepaliveTaskEnabled($Task) {
  return ($null -ne $Task) -and ([string]$Task.State -ne 'Disabled')
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$same = $false
if ($existing) {
  $a = @($existing.Actions)[0]
  $same = ($a.Execute -eq $powershell) -and ($a.Arguments -eq $arguments) -and
    (Test-HiveTaskAccountMatches -Actual ([string]$existing.Principal.UserId) -Expected $windowsUser) -and
    ([string]$existing.Principal.LogonType -eq [string]$principal.LogonType) -and
    (Test-HiveKeepaliveTaskEnabled $existing) -and
    ((Get-HiveTriggerFingerprint $existing.Triggers) -eq (Get-HiveTriggerFingerprint $triggers))
}

if ($DryRun) {
  if ($same) { Write-Output "Unchanged: scheduled task '$TaskName' already matches"; return }
  Write-Output "Would register scheduled task '$TaskName':"
  Write-Output "  run:      $powershell $arguments"
  Write-Output "  triggers: $triggerText"
  Write-Output "  user:     $windowsUser ($logonText)"
  return
}

if ($same) { Write-Output "Unchanged: '$TaskName' already registered"; return }

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
  -Principal $principal -Settings $settings -Force | Out-Null
Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
Write-Output "Registered '$TaskName'"
