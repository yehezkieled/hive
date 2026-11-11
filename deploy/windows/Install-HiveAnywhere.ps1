<#
.SYNOPSIS
  One sitting at the PC: make it come back by itself after a restart.

.DESCRIPTION
  Run once as administrator, by the PC's owner, signed in as themselves.
  Safe to run again: every step checks first and changes only what differs.
  At the end it prints what it changed, what was already right, and what is
  left for a person to do.

    a. Tailscale unattended mode on (stays on the tailnet before sign-in).
    b. Auto sign-in then lock: ARSO "Always", Sysinternals Autologon for cold
       boots, and a logon task that locks the screen at once.
    c. WSL keep-alive task with a restart loop (Register-HiveWsl.ps1
       -LogonOnly); then the old Startup-folder wsl-keepalive.vbs is moved out.
    d. %UserProfile%\.wslconfig with the idle time-outs off.
    e. The narrow admin helper: one on-demand SYSTEM task that runs a fixed
       verb list (HiveAdminHelper.ps1).
    f. Printed checklist for what a script cannot do (BIOS, Tailscale key
       expiry).

  -WhatIf previews every step and changes nothing (no password prompt).

  The Windows password is asked for once, only when Autologon is not set up
  yet (or -ResetAutologon). It is handed only to Autologon64.exe, which stores
  it as an LSA secret. It is never printed, logged or written to a file.

  Steps and caveats: docs/anywhere-sitting.md, deploy/windows/README.md.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Install-HiveAnywhere.ps1 -WhatIf
  powershell -ExecutionPolicy Bypass -File .\Install-HiveAnywhere.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  # WSL distro and Linux user that the keep-alive holds.
  [string]$Distro = 'Ubuntu',
  [string]$LinuxUser = 'hezki',
  # Path to Autologon64.exe. Default: look next to this script, then
  # C:\Program Files\Hive\Sysinternals, then download it from Sysinternals
  # and check its Microsoft signature.
  [string]$AutologonPath,
  # Ask for the password again even if Autologon is already set (password changed).
  [switch]$ResetAutologon
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:Results = New-Object System.Collections.Generic.List[object]
function Add-Result([string]$Step, [string]$Status, [string]$Detail) {
  $script:Results.Add([pscustomobject]@{ Step = $Step; Status = $Status; Detail = $Detail })
  $color = switch ($Status) {
    'changed' { 'Green' } 'unchanged' { 'Gray' } 'would change' { 'Cyan' }
    'manual' { 'Yellow' } default { 'Red' }
  }
  Write-Host ("  [{0}] {1}: {2}" -f $Status, $Step, $Detail) -ForegroundColor $color
}

function Invoke-Step([string]$Name, [scriptblock]$Body) {
  Write-Host ''
  Write-Host "== $Name" -ForegroundColor White
  try { & $Body } catch { Add-Result $Name 'failed' $_.Exception.Message }
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$userSid = $identity.User.Value
$windowsUser = $identity.Name
$system32 = [Environment]::GetFolderPath('System')
$programFiles = [Environment]::GetFolderPath('ProgramFiles')
$programData = [Environment]::GetFolderPath('CommonApplicationData')
$dryRun = [bool]$WhatIfPreference

Write-Host "Hive anywhere: one-sitting setup for $windowsUser" -ForegroundColor White
if ($dryRun) { Write-Host 'Preview only (-WhatIf): nothing will be changed.' -ForegroundColor Cyan }
if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
  Write-Host 'Use the 64-bit Windows PowerShell (not "Windows PowerShell (x86)").' -ForegroundColor Red
  exit 2
}
if (-not $isAdmin) {
  if (-not $dryRun) {
    Write-Host 'Run this from PowerShell opened with "Run as administrator".' -ForegroundColor Red
    exit 2
  }
  Write-Host 'Not elevated: the preview may show some checks as failed.' -ForegroundColor Yellow
}

# ---------------------------------------------------------------- helpers

function Set-RegistryValue([string]$Step, [string]$Key, [string]$Name, $Value, [string]$Type) {
  $current = $null
  $currentType = $null
  if (Test-Path -LiteralPath $Key) {
    $p = Get-ItemProperty -LiteralPath $Key -Name $Name -ErrorAction SilentlyContinue
    if ($p) {
      $current = $p.$Name
      $currentType = [string](Get-Item -LiteralPath $Key).GetValueKind($Name)
    }
  }
  if (Test-HiveRegistryValueMatches $current $Value $currentType $Type) {
    Add-Result $Step 'unchanged' "$Name is already $Value"
    return
  }
  if ($PSCmdlet.ShouldProcess("$Key\$Name", "set to $Value")) {
    if (-not (Test-Path -LiteralPath $Key)) { New-Item -Path $Key -Force | Out-Null }
    New-ItemProperty -LiteralPath $Key -Name $Name -Value $Value -PropertyType $Type -Force | Out-Null
    Add-Result $Step 'changed' "$Name set to $Value (was $(if ($null -eq $current) { 'unset' } else { $current }))"
  } else {
    Add-Result $Step 'would change' "$Name to $Value (now $(if ($null -eq $current) { 'unset' } else { $current }))"
  }
}

function Test-HiveRegistryValueMatches($Current, $Value, [string]$CurrentType, [string]$RequiredType) {
  return ($null -ne $Current) -and ("$Current" -ceq "$Value") -and ($CurrentType -ceq $RequiredType)
}

function Test-HiveTaskEnabled($Task) {
  return ($null -ne $Task) -and ([string]$Task.State -ne 'Disabled')
}

function Test-HiveAutologonConfigured($Winlogon, [string]$User, [string]$Domain) {
  $auto = $Winlogon.PSObject.Properties['AutoAdminLogon']
  $userName = $Winlogon.PSObject.Properties['DefaultUserName']
  $domainName = $Winlogon.PSObject.Properties['DefaultDomainName']
  return ($auto -and "$($auto.Value)" -eq '1') -and
    ($userName -and "$($userName.Value)" -ieq $User) -and
    ($domainName -and "$($domainName.Value)" -ieq $Domain)
}

function Get-TaskFingerprint($Task) {
  if (-not $Task) { return '' }
  $a = @($Task.Actions)[0]
  $t = @($Task.Triggers | ForEach-Object {
    $class = $_.CimClass.CimClassName
    $user = if ($class -eq 'MSFT_TaskLogonTrigger') { [string]$_.UserId } else { '' }
    "$class|$user"
  }) -join ','
  # Task Scheduler may report "PC\user" or just "user"; compare the name only.
  $uid = ([string]$Task.Principal.UserId -split '\\')[-1]
  return "$($a.Execute)|$($a.Arguments)|$uid|$($Task.Principal.LogonType)|$($Task.Principal.RunLevel)|$t"
}

function Set-HiveTask([string]$Step, [string]$Path, [string]$Name, $Action, $Trigger, $Principal, $Settings, [string]$Expected) {
  $existing = Get-ScheduledTask -TaskPath $Path -TaskName $Name -ErrorAction SilentlyContinue
  if ($existing -and (Get-TaskFingerprint $existing) -eq $Expected -and (Test-HiveTaskEnabled $existing)) {
    Add-Result $Step 'unchanged' "task '$Path$Name' already registered and enabled"
    return
  }
  if ($PSCmdlet.ShouldProcess("$Path$Name", 'register scheduled task')) {
    $params = @{ TaskPath = $Path; TaskName = $Name; Action = $Action; Principal = $Principal; Settings = $Settings; Force = $true }
    if ($null -ne $Trigger) { $params.Trigger = $Trigger }
    Register-ScheduledTask @params | Out-Null
    Enable-ScheduledTask -TaskPath $Path -TaskName $Name -ErrorAction Stop | Out-Null
    Add-Result $Step 'changed' "task '$Path$Name' $(if ($existing) { 'updated' } else { 'registered' })"
  } else {
    Add-Result $Step 'would change' "register task '$Path$Name'"
  }
}

function ConvertTo-WindowsArgument([string]$Value) {
  # Quote one argument for the MSVC/CommandLineToArgvW rules.
  if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
  $sb = New-Object System.Text.StringBuilder
  [void]$sb.Append('"')
  $slashes = 0
  foreach ($ch in $Value.ToCharArray()) {
    if ($ch -eq '\') { $slashes++; continue }
    if ($ch -eq '"') { [void]$sb.Append('\', 2 * $slashes + 1); [void]$sb.Append('"') }
    else { if ($slashes) { [void]$sb.Append('\', $slashes) }; [void]$sb.Append($ch) }
    $slashes = 0
  }
  if ($slashes) { [void]$sb.Append('\', 2 * $slashes) }
  [void]$sb.Append('"')
  return $sb.ToString()
}

function New-HiveAcl([bool]$Directory, [string[]]$ReadSids, [string[]]$ModifySids) {
  # Protected (no inheritance from the parent): SYSTEM and Administrators
  # full control, read for $ReadSids, modify for $ModifySids. Owner Administrators.
  if ($Directory) { $acl = New-Object System.Security.AccessControl.DirectorySecurity }
  else { $acl = New-Object System.Security.AccessControl.FileSecurity }
  $acl.SetAccessRuleProtection($true, $false)
  $acl.SetOwner((New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')))
  if ($Directory) {
    $inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
  } else {
    $inherit = [System.Security.AccessControl.InheritanceFlags]::None
  }
  $none = [System.Security.AccessControl.PropagationFlags]::None
  $rules = @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'))
  foreach ($s in $ReadSids) { $rules += , @($s, 'ReadAndExecute') }
  foreach ($s in $ModifySids) { $rules += , @($s, 'Modify') }
  foreach ($r in $rules) {
    $sid = New-Object System.Security.Principal.SecurityIdentifier($r[0])
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid, $r[1], $inherit, $none, 'Allow')))
  }
  return $acl
}

function Get-AclShape($Acl) {
  # Owner, protection and the explicit rules, in a stable order.
  $sid = [System.Security.Principal.SecurityIdentifier]
  $rules = @($Acl.GetAccessRules($true, $false, $sid) | ForEach-Object {
      '{0}|{1}|{2}|{3}|{4}' -f $_.IdentityReference.Value, [int]$_.FileSystemRights, [int]$_.InheritanceFlags, [int]$_.PropagationFlags, $_.AccessControlType
    } | Sort-Object)
  $owner = $Acl.GetOwner($sid).Value
  return "$owner|$($Acl.AreAccessRulesProtected)|$($rules -join ';')"
}

function Set-HiveAcl([string]$Step, [string]$Path, [bool]$Directory, [string[]]$ReadSids, [string[]]$ModifySids) {
  $want = New-HiveAcl $Directory $ReadSids $ModifySids
  $have = $null
  if (Test-Path -LiteralPath $Path) { $have = Get-Acl -LiteralPath $Path }
  if ($have -and (Get-AclShape $have) -eq (Get-AclShape $want)) { Add-Result $Step 'unchanged' "permissions on $Path"; return }
  if ($PSCmdlet.ShouldProcess($Path, 'set admin-only permissions')) {
    Set-Acl -LiteralPath $Path -AclObject $want
    Add-Result $Step 'changed' "permissions on $Path"
  } else {
    Add-Result $Step 'would change' "permissions on $Path"
  }
}

function New-HiveDirectory([string]$Step, [string]$Path) {
  if (Test-Path -LiteralPath $Path -PathType Container) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "$Path is a link; remove it and run again" }
    return
  }
  if ($PSCmdlet.ShouldProcess($Path, 'create folder')) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Add-Result $Step 'changed' "created $Path"
  } else {
    Add-Result $Step 'would change' "create $Path"
  }
}

function Copy-HiveFile([string]$Step, [string]$Source, [string]$Destination) {
  if (-not (Test-Path -LiteralPath $Source)) { throw "missing $Source" }
  if ((Test-Path -LiteralPath $Destination) -and
      (Get-FileHash -LiteralPath $Source).Hash -eq (Get-FileHash -LiteralPath $Destination).Hash) {
    Add-Result $Step 'unchanged' "$Destination is current"
    return
  }
  if ($PSCmdlet.ShouldProcess($Destination, "copy from $Source")) {
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
    Add-Result $Step 'changed' "installed $Destination"
  } else {
    Add-Result $Step 'would change' "install $Destination"
  }
}

function Merge-IniLines([string[]]$Lines, $Want) {
  # Sets section/key=value pairs in INI text, keeping every other line as is.
  # Returns @{ Lines = [string[]]; Changed = [string[]] }.
  $out = New-Object System.Collections.Generic.List[string]
  foreach ($l in $Lines) { $out.Add($l) }
  $changed = @()
  foreach ($section in $Want.Keys) {
    foreach ($key in $Want[$section].Keys) {
      $value = $Want[$section][$key]
      $current = $null; $start = -1; $end = $out.Count; $keyAt = -1
      for ($i = 0; $i -lt $out.Count; $i++) {
        $t = $out[$i].Trim()
        if ($t -match '^\[(.+)\]$') {
          if ($start -ge 0) { $end = $i; break }
          if ($Matches[1].Trim() -ieq $section) { $start = $i }
        } elseif ($start -ge 0 -and $t -match ('^' + [regex]::Escape($key) + '\s*=\s*(.*)$')) {
          $keyAt = $i; $current = $Matches[1].Trim()
        }
      }
      if ($keyAt -ge 0 -and $current -eq $value) { continue }
      if ($keyAt -ge 0) { $out[$keyAt] = "$key=$value" }
      elseif ($start -ge 0) {
        # Insert after the section's last non-blank line.
        $at = $end
        while ($at - 1 -gt $start -and -not $out[$at - 1].Trim()) { $at-- }
        $out.Insert($at, "$key=$value")
      } else {
        if ($out.Count -and $out[$out.Count - 1].Trim()) { $out.Add('') }
        $out.Add("[$section]"); $out.Add("$key=$value")
      }
      $changed += "[$section] $key=$value"
    }
  }
  return @{ Lines = [string[]]$out.ToArray(); Changed = [string[]]$changed }
}

# ---------------------------------------------------------------- a. Tailscale

$tailscale = Join-Path $programFiles 'Tailscale\tailscale.exe'
Invoke-Step 'a. Tailscale unattended' {
    if (-not (Test-Path -LiteralPath $tailscale)) { throw "Tailscale not found at $tailscale" }
    $prefs = (& $tailscale debug prefs 2>$null | Out-String)
    $on = $false
    try { $on = [bool]((ConvertFrom-Json $prefs).ForceDaemon) } catch { $on = $false }
    if ($on) { Add-Result 'a. Tailscale unattended' 'unchanged' 'unattended mode already on'; return }
    if ($PSCmdlet.ShouldProcess('Tailscale', 'tailscale set --unattended=true')) {
      $out = & $tailscale set --unattended=true 2>&1 | Out-String
      if ($LASTEXITCODE -ne 0) { throw "tailscale set failed: $($out.Trim())" }
      $after = $false
      try { $after = [bool]((ConvertFrom-Json (& $tailscale debug prefs 2>$null | Out-String)).ForceDaemon) } catch { $after = $false }
      if (-not $after) { throw 'tailscale set ran but unattended mode still reads off' }
      Add-Result 'a. Tailscale unattended' 'changed' 'unattended mode on'
    } else {
      Add-Result 'a. Tailscale unattended' 'would change' 'turn unattended mode on'
    }
}

# ---------------------------------------------------------------- b. Auto sign-in then lock

  Invoke-Step 'b1. ARSO "Always"' {
    $policy = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
    # Policy "Sign-in and lock last interactive user automatically after a restart": 0 = enabled.
    Set-RegistryValue 'b1. ARSO "Always"' $policy 'DisableAutomaticRestartSignOn' 0 'DWord'
    # Policy "Configure the mode of ... after a restart or cold boot": 1 = Always.
    Set-RegistryValue 'b1. ARSO "Always"' $policy 'AutomaticRestartSignOnConfig' 1 'DWord'
  }

  Invoke-Step 'b2. Autologon (cold boots)' {
    $step = 'b2. Autologon (cold boots)'
    $winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    $wl = Get-ItemProperty -LiteralPath $winlogon
    $samName = $windowsUser.Split('\')[-1]
    $domain = $windowsUser.Split('\')[0]
    $already = Test-HiveAutologonConfigured $wl $samName $domain
    if ($already -and -not $ResetAutologon) {
      Add-Result $step 'unchanged' "auto sign-in already set for $samName (use -ResetAutologon after a password change)"
      return
    }
    if ($dryRun) {
      Add-Result $step 'would change' "ask for the password once and set Autologon for $samName"
      return
    }

    # Find or fetch Autologon64.exe and require a valid Microsoft signature.
    $candidates = @()
    if ($AutologonPath) { $candidates += $AutologonPath }
    $candidates += (Join-Path $PSScriptRoot 'Autologon64.exe')
    $candidates += (Join-Path $programFiles 'Hive\Sysinternals\Autologon64.exe')
    $exe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $exe) {
      $dir = Join-Path $programFiles 'Hive\Sysinternals'
      $exe = Join-Path $dir 'Autologon64.exe'
      if (-not $PSCmdlet.ShouldProcess($exe, 'download Autologon64.exe from live.sysinternals.com')) { return }
      New-Item -ItemType Directory -Path $dir -Force | Out-Null
      [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
      Invoke-WebRequest -UseBasicParsing -Uri 'https://live.sysinternals.com/Autologon64.exe' -OutFile $exe
      Add-Result $step 'changed' "downloaded $exe"
    }
    $sig = Get-AuthenticodeSignature -LiteralPath $exe
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
      throw "$exe is not validly signed by Microsoft ($($sig.Status)); not running it"
    }

    Write-Host "  Windows password for $windowsUser (typed, not shown; used only by Autologon)." -ForegroundColor Yellow
    Write-Host '  For a Microsoft account this is the account password, not the PIN.' -ForegroundColor Yellow
    $secure = $null
    for ($try = 1; $try -le 3; $try++) {
      $first = Read-Host -AsSecureString '  Password'
      $second = Read-Host -AsSecureString '  Same password again'
      $b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($first)
      $b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($second)
      try {
        $match = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b1) -ceq [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b2)
        $empty = $first.Length -eq 0
      } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2)
      }
      $second.Dispose()
      if ($match -and -not $empty) { $secure = $first; break }
      $first.Dispose()
      Write-Host '  The two entries differ or are empty. Try again.' -ForegroundColor Red
    }
    if (-not $secure) { Add-Result $step 'failed' 'no password entered; auto sign-in not set'; return }

    # Hand the password to Autologon through a .NET process start, so no
    # PowerShell logging (transcripts, module logging) sees the argument.
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
      $psi = New-Object System.Diagnostics.ProcessStartInfo
      $psi.FileName = $exe
      $psi.UseShellExecute = $false
      $psi.CreateNoWindow = $true
      $psi.Arguments = (ConvertTo-WindowsArgument $samName) + ' ' + (ConvertTo-WindowsArgument $domain) + ' ' +
        (ConvertTo-WindowsArgument ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr))) + ' /accepteula'
      $proc = [System.Diagnostics.Process]::Start($psi)
      $psi.Arguments = ''
      $proc.WaitForExit()
      $code = $proc.ExitCode
    } finally {
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
      $secure.Dispose()
      $psi = $null
    }

    $wl = Get-ItemProperty -LiteralPath $winlogon
    $ok = Test-HiveAutologonConfigured $wl $samName $domain
    $plain = $wl.PSObject.Properties['DefaultPassword']
    if ($plain) {
      # Autologon keeps the password as an LSA secret; a plain-text copy must not linger.
      Remove-ItemProperty -LiteralPath $winlogon -Name 'DefaultPassword'
      Add-Result $step 'changed' 'removed a plain-text DefaultPassword from the registry'
    }
    if (-not $ok) { throw "Autologon exit $code and AutoAdminLogon is not 1" }
    Add-Result $step 'changed' "auto sign-in set for $samName (password stored by Autologon as an LSA secret)"
  }

  Invoke-Step 'b3. Lock at logon' {
    $rundll = Join-Path $system32 'rundll32.exe'
    $action = New-ScheduledTaskAction -Execute $rundll -Argument 'user32.dll,LockWorkStation'
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $windowsUser
    $principal = New-ScheduledTaskPrincipal -UserId $windowsUser -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
      -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    $expected = "$rundll|user32.dll,LockWorkStation|$($windowsUser.Split('\')[-1])|Interactive|Limited|MSFT_TaskLogonTrigger|$windowsUser"
    Set-HiveTask 'b3. Lock at logon' '\Hive\' 'Hive Lock At Logon' $action $trigger $principal $settings $expected
}

# ---------------------------------------------------------------- c. Keep-alive

  Invoke-Step 'c1. WSL keep-alive task' {
    $register = Join-Path $PSScriptRoot 'Register-HiveWsl.ps1'
    $args2 = @{ Distro = $Distro; User = $LinuxUser; LogonOnly = $true }
    if ($dryRun) { $args2.DryRun = $true }
    $out = (& $register @args2 | Out-String).Trim()
    if ($out -like 'Unchanged*') { Add-Result 'c1. WSL keep-alive task' 'unchanged' $out }
    elseif ($out -like 'Would*') { Add-Result 'c1. WSL keep-alive task' 'would change' ($out -replace '\s+', ' ') }
    else { Add-Result 'c1. WSL keep-alive task' 'changed' $out }
  }

  Invoke-Step 'c2. Retire Startup-folder script' {
    $step = 'c2. Retire Startup-folder script'
    $startup = [Environment]::GetFolderPath('Startup')
    $vbs = Join-Path $startup 'wsl-keepalive.vbs'
    if (-not (Test-Path -LiteralPath $vbs)) { Add-Result $step 'unchanged' "no $vbs"; return }
    $task = Get-ScheduledTask -TaskName 'Hive WSL Keepalive' -ErrorAction SilentlyContinue
    if (-not (Test-HiveTaskEnabled $task)) {
      if ($dryRun) { Add-Result $step 'would change' "move $vbs out once the keep-alive task is registered"; return }
      Add-Result $step 'failed' "keep-alive task not registered; left $vbs in place"
      return
    }
    $backup = Join-Path $env:LOCALAPPDATA 'Hive\retired-startup'
    if ($PSCmdlet.ShouldProcess($vbs, "move to $backup")) {
      New-Item -ItemType Directory -Path $backup -Force | Out-Null
      Move-Item -LiteralPath $vbs -Destination (Join-Path $backup 'wsl-keepalive.vbs') -Force
      Add-Result $step 'changed' "moved $vbs to $backup (the running sleep it started is left alone)"
    } else {
      Add-Result $step 'would change' "move $vbs to $backup"
    }
}

# ---------------------------------------------------------------- d. .wslconfig

  Invoke-Step 'd. .wslconfig idle time-outs off' {
    $step = 'd. .wslconfig idle time-outs off'
    $path = Join-Path $env:USERPROFILE '.wslconfig'
    $want = [ordered]@{ 'wsl2' = [ordered]@{ 'vmIdleTimeout' = '-1' }; 'general' = [ordered]@{ 'instanceIdleTimeout' = '-1' } }
    $old = @()
    if (Test-Path -LiteralPath $path) { $old = [System.IO.File]::ReadAllLines($path) }
    $merged = Merge-IniLines $old $want
    $lines = $merged.Lines
    $changed = $merged.Changed
    if (-not $changed) { Add-Result $step 'unchanged' "$path already has the idle time-outs off"; return }
    if ($PSCmdlet.ShouldProcess($path, "set $($changed -join ', ')")) {
      if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination "$path.bak-hive" -Force }
      [System.IO.File]::WriteAllLines($path, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
      Add-Result $step 'changed' "$($changed -join ', ') in $path (takes effect at the next WSL start)"
    } else {
      Add-Result $step 'would change' "$($changed -join ', ') in $path"
    }
}

# ---------------------------------------------------------------- e. Admin helper

  Invoke-Step 'e. Admin helper' {
    $step = 'e. Admin helper'
    $users = 'S-1-5-32-545'
    $hiveDir = Join-Path $programFiles 'Hive'
    $binDir = Join-Path $hiveDir 'AdminHelper'
    $helper = Join-Path $binDir 'HiveAdminHelper.ps1'
    $dataDir = Join-Path $programData 'HiveAdmin'
    $requestDir = Join-Path $dataDir 'request'
    $stateDir = Join-Path $dataDir 'state'

    # Code: writable by administrators only, so nothing running as the user can change what SYSTEM runs.
    foreach ($d in @($hiveDir, $binDir)) { New-HiveDirectory $step $d; if (Test-Path $d) { Set-HiveAcl $step $d $true @($users) @() } }
    Copy-HiveFile $step (Join-Path $PSScriptRoot 'HiveAdminHelper.ps1') $helper
    if (Test-Path -LiteralPath $helper) { Set-HiveAcl $step $helper $false @($users) @() }

    # Data: audit log readable by users; request folder writable by this user only; state admin-only.
    New-HiveDirectory $step $dataDir
    if (Test-Path $dataDir) { Set-HiveAcl $step $dataDir $true @($users) @() }
    New-HiveDirectory $step $requestDir
    if (Test-Path $requestDir) { Set-HiveAcl $step $requestDir $true @($users) @($userSid) }
    New-HiveDirectory $step $stateDir
    if (Test-Path $stateDir) { Set-HiveAcl $step $stateDir $true @() @() }

    $powershell = Join-Path $system32 'WindowsPowerShell\v1.0\powershell.exe'
    $arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$helper`""
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
    $principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
      -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances Queue
    $expected = "$powershell|$arguments|SYSTEM|ServiceAccount|Highest|"
    Set-HiveTask $step '\Hive\' 'Hive Admin Helper' $action $null $principal $settings $expected

    # Let this user (and only this user, besides admins) start it on demand: read + execute, no write.
    $sddl = "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;$userSid)"
    $svc = New-Object -ComObject 'Schedule.Service'
    $svc.Connect()
    $task = $null
    try { $task = $svc.GetFolder('\Hive').GetTask('Hive Admin Helper') } catch { $task = $null }
    if (-not $task) {
      if ($dryRun) { Add-Result $step 'would change' 'allow this user to start the helper task'; return }
      throw 'helper task not found after registering'
    }
    $have = $task.GetSecurityDescriptor(4)
    if ($have -eq $sddl) { Add-Result $step 'unchanged' 'this user may start the helper task'; return }
    if ($PSCmdlet.ShouldProcess('\Hive\Hive Admin Helper', 'allow this user to start it')) {
      $task.SetSecurityDescriptor($sddl, 0)
      Add-Result $step 'changed' 'this user may start the helper task (read and run only)'
    } else {
      Add-Result $step 'would change' 'allow this user to start the helper task'
    }
}

# ---------------------------------------------------------------- f. Manual checklist

Write-Host ''
Write-Host '== f. Left for a person (a script cannot do these)' -ForegroundColor White
Add-Result 'f1. BIOS' 'manual' 'enable power-on when AC power returns (often "Restore on AC Power Loss" = Power On; the name varies by vendor)'
Add-Result 'f2. Tailscale key expiry' 'manual' 'in the Tailscale admin console (phone is fine), Machines > this PC > Disable key expiry'
Add-Result 'f3. Windows Hello only' 'manual' 'if Settings > Accounts > Sign-in options has "only allow Windows Hello sign-in" on, turn it off, or auto sign-in may not work'
Add-Result 'f4. Claude sign-in' 'manual' 'in WSL: claude, then /login (expires 21 Nov 2026 about 16:51 AEDT)'

# ---------------------------------------------------------------- summary

Write-Host ''
Write-Host '== Summary' -ForegroundColor White
$script:Results | Format-Table -AutoSize -Wrap Step, Status, Detail | Out-String -Width 200 | Write-Host
$failed = @($script:Results | Where-Object { $_.Status -eq 'failed' }).Count
$changed = @($script:Results | Where-Object { $_.Status -eq 'changed' }).Count
Write-Host ("changed={0} failed={1}. Next: docs/anywhere-sitting.md (test restart)." -f $changed, $failed)
if ($failed -gt 0) { exit 1 }
exit 0
