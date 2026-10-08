<#
.SYNOPSIS
  Registers (or removes) the Task Scheduler task that keeps WSL - and so the
  Hive systemd user services - running from boot.

.DESCRIPTION
  WSL's VM exits when no process is attached. The task starts a hidden,
  long-lived `wsl.exe ... sleep infinity` at startup and at logon; with
  systemd=true in /etc/wsl.conf and linger enabled, that brings up
  hive-gateway.service, clip-desk.service and the hive-fleet-up timer. Task Scheduler restarts
  the task if it ends.

  -DryRun prints what would be registered and changes nothing.
  Run from an elevated PowerShell. See docs/fleet-up.md.
#>
[CmdletBinding()]
param(
  [string]$Distro = 'Ubuntu',
  [string]$User = 'hezki',
  [string]$TaskName = 'Hive WSL Keepalive',
  [switch]$Uninstall,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if ($Uninstall) {
  if ($DryRun) { Write-Output "Would unregister scheduled task '$TaskName'"; return }
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Output "Unregistered '$TaskName'"
  return
}

$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
$arguments = "-d $Distro -u $User --exec /usr/bin/sleep infinity"
$action = New-ScheduledTaskAction -Execute $wsl -Argument $arguments
$triggers = @(
  (New-ScheduledTaskTrigger -AtStartup),
  (New-ScheduledTaskTrigger -AtLogOn)
)
# Run as the interactive user whether or not logged on needs a stored password;
# a SYSTEM-owned task cannot see the user's distro, so use the user at logon
# plus the startup trigger via S4U (no password stored).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -MultipleInstances IgnoreNew `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden

if ($DryRun) {
  Write-Output "Would register scheduled task '$TaskName':"
  Write-Output "  run:      $wsl $arguments"
  Write-Output "  triggers: at startup, at logon"
  Write-Output "  user:     $env:USERDOMAIN\$env:USERNAME (S4U)"
  return
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
  -Principal $principal -Settings $settings -Force | Out-Null
Write-Output "Registered '$TaskName'"
