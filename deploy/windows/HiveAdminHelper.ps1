<#
.SYNOPSIS
  The narrow Windows admin helper: runs one verb from a fixed allow-list.

.DESCRIPTION
  Installed by Install-HiveAnywhere.ps1 to
  C:\Program Files\Hive\AdminHelper\ (writable by administrators only) and run
  by the on-demand scheduled task "\Hive\Hive Admin Helper" as SYSTEM.

  The task takes no arguments. The helper reads ONE request line from
  C:\ProgramData\HiveAdmin\request\request.txt:

      <nonce> <verb> [<number>]

  - nonce:  8-64 characters, letters, digits, '-' or '_'. A nonce already
            handled is ignored, so starting the task twice runs nothing twice.
  - verb:   one of the allow-list below. Nothing else is ever run.
  - number: only for verbs that take one, digits only, range-checked.

  Anything else is rejected and logged as rejected, without echoing the text.
  Every call appends one line to C:\ProgramData\HiveAdmin\audit.log.

  The helper never runs text from the request. It only uses the request to
  pick one of the fixed handlers in Invoke-HiveAdminVerb, and the number is
  passed on only as a range-checked [int].

  See docs/anywhere-sitting.md and deploy/windows/README.md.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# BEGIN ALLOW-LIST (tests parse this block: one verb per line, 'verb' = 'argument rule')
$script:HiveAdminVerbs = [ordered]@{
  'ping'              = 'none'
  'restart-wsl'       = 'none'
  'restart-tailscale' = 'none'
  'tailscale-up'      = 'none'
  'restart-pc'        = 'hour-optional'
  'pause-updates'     = 'days-optional'
}
# END ALLOW-LIST

# BEGIN REQUEST-GRAMMAR (tests parse this block)
$script:HiveAdminRequestPattern = '^([A-Za-z0-9][A-Za-z0-9_-]{7,63}) ([a-z][a-z-]{0,31})(?: ([0-9]{1,2}))?$'
$script:HiveAdminMaxRequestBytes = 128
$script:HiveAdminHourMin = 0
$script:HiveAdminHourMax = 23
$script:HiveAdminDaysMin = 1
$script:HiveAdminDaysMax = 35
$script:HiveAdminDaysDefault = 7
# END REQUEST-GRAMMAR

$script:HiveAdminRoot = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'HiveAdmin'

function ConvertFrom-HiveAdminRequest {
  <#
    Validates raw request bytes. Returns @{ Nonce; Verb; Arg } where Arg is
    $null or an [int] already range-checked. Throws 'rejected: <reason>' on
    anything else. The reason never contains request text.
  #>
  [CmdletBinding()]
  param([AllowNull()][byte[]]$Bytes)

  if ($null -eq $Bytes -or $Bytes.Length -eq 0) { throw 'rejected: empty request' }
  if ($Bytes.Length -gt $script:HiveAdminMaxRequestBytes) { throw 'rejected: request too long' }

  $end = $Bytes.Length
  if ($end -ge 1 -and $Bytes[$end - 1] -eq 10) { $end-- }
  if ($end -ge 1 -and $Bytes[$end - 1] -eq 13) { $end-- }
  if ($end -eq 0) { throw 'rejected: empty request' }
  for ($i = 0; $i -lt $end; $i++) {
    $b = $Bytes[$i]
    if ($b -lt 0x20 -or $b -gt 0x7E) { throw 'rejected: request must be one line of printable ASCII' }
  }
  $text = [System.Text.Encoding]::ASCII.GetString($Bytes, 0, $end)

  $m = [regex]::Match($text, $script:HiveAdminRequestPattern, [System.Text.RegularExpressions.RegexOptions]::CultureInvariant)
  if (-not $m.Success) { throw 'rejected: request does not match <nonce> <verb> [<number>]' }

  $nonce = $m.Groups[1].Value
  $verb = $m.Groups[2].Value
  $hasArg = $m.Groups[3].Success

  # Case-sensitive, exact lookup; the ordered dictionary compares keys
  # case-insensitively, so compare the stored key too.
  $rule = $null
  foreach ($key in $script:HiveAdminVerbs.Keys) {
    if ([string]::Equals($key, $verb, [System.StringComparison]::Ordinal)) { $rule = $script:HiveAdminVerbs[$key] }
  }
  if ($null -eq $rule) { throw 'rejected: unknown verb' }

  $arg = $null
  switch ($rule) {
    'none' {
      if ($hasArg) { throw "rejected: $verb takes no number" }
    }
    'hour-optional' {
      if ($hasArg) {
        $arg = [int]::Parse($m.Groups[3].Value, [System.Globalization.CultureInfo]::InvariantCulture)
        if ($arg -lt $script:HiveAdminHourMin -or $arg -gt $script:HiveAdminHourMax) { throw 'rejected: hour must be 0-23' }
      }
    }
    'days-optional' {
      if ($hasArg) {
        $arg = [int]::Parse($m.Groups[3].Value, [System.Globalization.CultureInfo]::InvariantCulture)
        if ($arg -lt $script:HiveAdminDaysMin -or $arg -gt $script:HiveAdminDaysMax) { throw 'rejected: days must be 1-35' }
      }
    }
    default { throw 'rejected: unknown argument rule' }
  }

  return @{ Nonce = $nonce; Verb = $verb; Arg = $arg }
}

function Get-HiveRestartDelaySeconds {
  <# Seconds from $Now until the next HH:00 local time (tomorrow if already past). #>
  [CmdletBinding()]
  param([Parameter(Mandatory)][int]$Hour, [Parameter(Mandatory)][datetime]$Now)
  if ($Hour -lt 0 -or $Hour -gt 23) { throw 'hour must be 0-23' }
  $target = $Now.Date.AddHours($Hour)
  if ($target -le $Now) { $target = $target.AddDays(1) }
  return [int][Math]::Ceiling(($target - $Now).TotalSeconds)
}

function Get-HivePauseWindow {
  <# Start and end (UTC, ISO 8601 'Z') for a Windows Update pause of $Days days. #>
  [CmdletBinding()]
  param([Parameter(Mandatory)][int]$Days, [Parameter(Mandatory)][datetime]$NowUtc)
  if ($Days -lt $script:HiveAdminDaysMin -or $Days -gt $script:HiveAdminDaysMax) { throw 'days must be 1-35' }
  $fmt = 'yyyy-MM-ddTHH:mm:ssZ'
  $inv = [System.Globalization.CultureInfo]::InvariantCulture
  return @{
    Start = $NowUtc.ToString($fmt, $inv)
    End   = $NowUtc.AddDays($Days).ToString($fmt, $inv)
  }
}

function Write-HiveAdminAudit {
  param([string]$Nonce, [string]$Verb, [string]$Arg, [string]$Result, [string]$Detail)
  $log = Join-Path $script:HiveAdminRoot 'audit.log'
  if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 1MB) {
    Microsoft.PowerShell.Management\Move-Item -LiteralPath $log -Destination "$log.1" -Force
  }
  # Detail is our own text plus truncated tool output; strip line breaks so one call is one line.
  $clean = ($Detail -replace '[\r\n\t]+', ' ' -replace '[^\x20-\x7E]', '?')
  if ($clean.Length -gt 400) { $clean = $clean.Substring(0, 400) + '...' }
  $stamp = [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:sszzz', [System.Globalization.CultureInfo]::InvariantCulture)
  $line = "$stamp nonce=$Nonce verb=$Verb arg=$Arg result=$Result detail=$clean"
  [System.IO.File]::AppendAllText($log, $line + "`r`n", [System.Text.Encoding]::ASCII)
}

function Invoke-HiveNative {
  # Runs a FIXED executable path with FIXED arguments built by the handlers below.
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string[]]$Arguments)
  $out = & $Path @Arguments 2>&1 | Out-String
  return @{ ExitCode = $LASTEXITCODE; Output = $out.Trim() }
}

function Invoke-HiveAdminVerb {
  [CmdletBinding()]
  param([Parameter(Mandatory)][string]$Verb, [AllowNull()]$Arg)

  $system32 = [Environment]::GetFolderPath('System')
  $programFiles = [Environment]::GetFolderPath('ProgramFiles')
  $tailscale = Join-Path $programFiles 'Tailscale\tailscale.exe'
  $shutdown = Join-Path $system32 'shutdown.exe'

  switch -CaseSensitive ($Verb) {
    'ping' {
      return 'pong'
    }
    'restart-wsl' {
      # SYSTEM cannot see the user's WSL VM through wsl.exe, so restart the
      # WSL service; the keep-alive task's loop starts the distro again.
      $names = @('WSLService', 'LxssManager')
      foreach ($name in $names) {
        $svc = Microsoft.PowerShell.Management\Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($null -ne $svc) {
          Microsoft.PowerShell.Management\Restart-Service -Name $name -Force
          return "restarted service $name; keep-alive restarts the distro"
        }
      }
      throw 'no WSL service found (WSLService, LxssManager)'
    }
    'restart-tailscale' {
      Microsoft.PowerShell.Management\Restart-Service -Name 'Tailscale' -Force
      return 'restarted service Tailscale'
    }
    'tailscale-up' {
      $r = Invoke-HiveNative -Path $tailscale -Arguments @('up', '--unattended', '--timeout=60s')
      if ($r.ExitCode -ne 0) { throw "tailscale up exit $($r.ExitCode): $($r.Output)" }
      return "tailscale up ok: $($r.Output)"
    }
    'restart-pc' {
      if ($null -eq $Arg) {
        $delay = 30
      } else {
        $delay = Get-HiveRestartDelaySeconds -Hour ([int]$Arg) -Now ([datetime]::Now)
      }
      # /g restarts and, with ARSO on, signs in and locks again.
      $r = Invoke-HiveNative -Path $shutdown -Arguments @('/g', '/t', ([string][int]$delay), '/d', 'p:4:1', '/c', 'Hive admin helper: restart requested')
      if ($r.ExitCode -ne 0) { throw "shutdown exit $($r.ExitCode): $($r.Output)" }
      return "restart scheduled in $delay s"
    }
    'pause-updates' {
      $days = $script:HiveAdminDaysDefault
      if ($null -ne $Arg) { $days = [int]$Arg }
      $w = Get-HivePauseWindow -Days $days -NowUtc ([datetime]::UtcNow)
      $key = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
      if (-not (Test-Path -LiteralPath $key)) { Microsoft.PowerShell.Management\New-Item -Path $key -Force | Out-Null }
      $values = [ordered]@{
        'PauseUpdatesStartTime'        = $w.Start
        'PauseUpdatesExpiryTime'       = $w.End
        'PauseFeatureUpdatesStartTime' = $w.Start
        'PauseFeatureUpdatesEndTime'   = $w.End
        'PauseQualityUpdatesStartTime' = $w.Start
        'PauseQualityUpdatesEndTime'   = $w.End
      }
      foreach ($name in $values.Keys) {
        Microsoft.PowerShell.Management\Set-ItemProperty -LiteralPath $key -Name $name -Value $values[$name] -Type String
      }
      return "updates paused until $($w.End)"
    }
    default {
      throw 'unknown verb'
    }
  }
}

function Test-HiveNotReparse {
  param([string]$Path)
  $item = Get-Item -LiteralPath $Path -Force
  return -not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
}

function Invoke-HiveAdminHelperMain {
  # Only system module paths; the elevated process never loads user modules.
  $env:PSModulePath = (Join-Path $PSHOME 'Modules')

  $requestDir = Join-Path $script:HiveAdminRoot 'request'
  $requestFile = Join-Path $requestDir 'request.txt'
  $stateFile = Join-Path (Join-Path $script:HiveAdminRoot 'state') 'last-nonce'

  $nonce = '-'; $verb = '-'; $argText = '-'
  try {
    if (-not (Test-Path -LiteralPath $requestFile -PathType Leaf)) {
      Write-HiveAdminAudit $nonce $verb $argText 'ignored' 'no request file'
      return
    }
    if (-not (Test-HiveNotReparse $script:HiveAdminRoot) -or -not (Test-HiveNotReparse $requestDir) -or -not (Test-HiveNotReparse $requestFile)) {
      Write-HiveAdminAudit $nonce $verb $argText 'rejected' 'request path is a link'
      return
    }
    if ((Get-Item -LiteralPath $requestFile).Length -gt $script:HiveAdminMaxRequestBytes) {
      Write-HiveAdminAudit $nonce $verb $argText 'rejected' 'request too long'
      return
    }
    # Read only; the helper never writes or deletes in the user-writable folder.
    $bytes = [System.IO.File]::ReadAllBytes($requestFile)
    try {
      $req = ConvertFrom-HiveAdminRequest -Bytes $bytes
    } catch {
      Write-HiveAdminAudit $nonce $verb $argText 'rejected' $_.Exception.Message
      return
    }
    $nonce = $req.Nonce; $verb = $req.Verb
    if ($null -ne $req.Arg) { $argText = [string]$req.Arg }

    $last = $null
    if (Test-Path -LiteralPath $stateFile) { $last = ([System.IO.File]::ReadAllText($stateFile)).Trim() }
    if ($last -eq $nonce) {
      Write-HiveAdminAudit $nonce $verb $argText 'ignored' 'nonce already handled'
      return
    }
    [System.IO.File]::WriteAllText($stateFile, $nonce, [System.Text.Encoding]::ASCII)

    Write-HiveAdminAudit $nonce $verb $argText 'started' ''
    $detail = Invoke-HiveAdminVerb -Verb $req.Verb -Arg $req.Arg
    Write-HiveAdminAudit $nonce $verb $argText 'ok' ([string]$detail)
  } catch {
    Write-HiveAdminAudit $nonce $verb $argText 'failed' $_.Exception.Message
    exit 1
  }
}

# Tests load the functions above from the syntax tree and never reach this line.
if ($MyInvocation.InvocationName -ne '.') { Invoke-HiveAdminHelperMain }
