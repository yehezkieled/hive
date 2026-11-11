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
            consumed in the last 200 requests is ignored.
  - verb:   one of the allow-list below. Nothing else is ever run.
  - number: only for verbs that take one, digits only, range-checked.

  Anything else is rejected and logged as rejected, without echoing the text.
  Every call appends one line to C:\ProgramData\HiveAdmin\audit.log.

  The helper removes the request file before validating or acting on it. It
  never runs text from the request. It only uses the request to
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
  $clean = $Detail -replace '(?i)\bhttps?://\S+', '[redacted]'
  $clean = $clean -replace '[A-Za-z0-9_\-+/=.]{20,}', '[redacted]'
  $clean = ($clean -replace '[\r\n\t]+', ' ' -replace '[^\x20-\x7E]', '?')
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

function Initialize-HiveAdminNative {
  if ('HiveAdminRequestFile' -as [type]) { return }
  $null = Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class HiveAdminRequestFile
{
    private const uint GENERIC_READ = 0x80000000;
    private const uint DELETE = 0x00010000;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
    private const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400;
    private const uint FILE_ATTRIBUTE_DIRECTORY = 0x00000010;
    private const uint FILE_TYPE_DISK = 1;
    private const int FILE_DISPOSITION_INFO_CLASS = 4;

    [StructLayout(LayoutKind.Sequential)]
    private struct FILETIME
    {
        public uint Low;
        public uint High;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BY_HANDLE_FILE_INFORMATION
    {
        public uint FileAttributes;
        public FILETIME CreationTime;
        public FILETIME LastAccessTime;
        public FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FILE_DISPOSITION_INFO
    {
        [MarshalAs(UnmanagedType.Bool)]
        public bool DeleteFile;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string name, uint access, uint share, IntPtr security, uint creation,
        uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint GetFileType(SafeFileHandle handle);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle, out BY_HANDLE_FILE_INFORMATION information);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandleW(
        SafeFileHandle handle, StringBuilder path, uint length, uint flags);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileSizeEx(SafeFileHandle handle, out long size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool ReadFile(
        SafeFileHandle handle, byte[] buffer, uint count, out uint read, IntPtr overlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetFileInformationByHandle(
        SafeFileHandle handle, int informationClass, ref FILE_DISPOSITION_INFO information,
        uint size);

    private static string NormalizePath(string path)
    {
        if (path.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
            path = @"\\" + path.Substring(8);
        else if (path.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase))
            path = path.Substring(4);
        return Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    private static Exception Win32Failure()
    {
        return new Win32Exception(Marshal.GetLastWin32Error());
    }

    public static byte[] Consume(string requestPath, string expectedPath, int maxBytes, out bool tooLong)
    {
        tooLong = false;
        using (SafeFileHandle handle = CreateFileW(
            requestPath, GENERIC_READ | DELETE, 0, IntPtr.Zero, OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT, IntPtr.Zero))
        {
            if (handle.IsInvalid) throw Win32Failure();
            if (GetFileType(handle) != FILE_TYPE_DISK) throw new IOException("request is not a disk file");

            BY_HANDLE_FILE_INFORMATION information;
            if (!GetFileInformationByHandle(handle, out information)) throw Win32Failure();
            if ((information.FileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) != 0)
                throw new IOException("request is a reparse point or directory");

            StringBuilder finalPath = new StringBuilder(32768);
            uint pathLength = GetFinalPathNameByHandleW(handle, finalPath, (uint)finalPath.Capacity, 0);
            if (pathLength == 0 || pathLength >= finalPath.Capacity) throw Win32Failure();
            if (!String.Equals(NormalizePath(finalPath.ToString()), NormalizePath(expectedPath), StringComparison.OrdinalIgnoreCase))
                throw new IOException("opened request is outside the request location");

            long length;
            if (!GetFileSizeEx(handle, out length)) throw Win32Failure();
            byte[] bytes = null;
            if (length > maxBytes)
            {
                tooLong = true;
            }
            else
            {
                bytes = new byte[(int)length];
                if (length > 0)
                {
                    uint read;
                    if (!ReadFile(handle, bytes, (uint)length, out read, IntPtr.Zero)) throw Win32Failure();
                    if (read != (uint)length) throw new IOException("request read was incomplete");
                }
            }

            FILE_DISPOSITION_INFO disposition = new FILE_DISPOSITION_INFO();
            disposition.DeleteFile = true;
            if (!SetFileInformationByHandle(
                handle, FILE_DISPOSITION_INFO_CLASS, ref disposition,
                (uint)Marshal.SizeOf(typeof(FILE_DISPOSITION_INFO))))
                throw Win32Failure();
            return bytes;
        }
    }
}
'@
}

function Add-HiveConsumedNonce {
  [CmdletBinding()]
  param([Parameter(Mandatory)][string]$Nonce, [Parameter(Mandatory)][string]$StateFile)

  $nonces = @()
  if (Test-Path -LiteralPath $StateFile -PathType Leaf) {
    $nonces = @([System.IO.File]::ReadAllLines($StateFile))
  }
  foreach ($existing in $nonces) {
    if ([string]::Equals($existing, $Nonce, [System.StringComparison]::Ordinal)) { return $false }
  }
  $nonces += $Nonce
  if ($nonces.Count -gt 200) { $nonces = @($nonces | Select-Object -Last 200) }
  [System.IO.File]::WriteAllLines($StateFile, [string[]]$nonces, [System.Text.Encoding]::ASCII)
  return $true
}

function Invoke-HiveAdminRequest {
  [CmdletBinding()]

  $requestDir = Join-Path $script:HiveAdminRoot 'request'
  $requestFile = Join-Path $requestDir 'request.txt'
  $stateFile = Join-Path (Join-Path $script:HiveAdminRoot 'state') 'consumed-nonces'

  $nonce = '-'; $verb = '-'; $argText = '-'
  try {
    if (-not (Test-Path -LiteralPath $requestFile -PathType Leaf)) {
      Write-HiveAdminAudit $nonce $verb $argText 'ignored' 'no request file'
      return $true
    }
    Initialize-HiveAdminNative
    $tooLong = $false
    try {
      $bytes = [HiveAdminRequestFile]::Consume(
        $requestFile, [System.IO.Path]::GetFullPath($requestFile),
        $script:HiveAdminMaxRequestBytes, [ref]$tooLong)
    } catch {
      Write-HiveAdminAudit $nonce $verb $argText 'rejected' 'request could not be safely opened and consumed'
      return $true
    }
    if ($tooLong) {
      Write-HiveAdminAudit $nonce $verb $argText 'rejected' 'request too long'
      return $true
    }
    try {
      $req = ConvertFrom-HiveAdminRequest -Bytes $bytes
    } catch {
      Write-HiveAdminAudit $nonce $verb $argText 'rejected' $_.Exception.Message
      return $true
    }
    $nonce = $req.Nonce; $verb = $req.Verb
    if ($null -ne $req.Arg) { $argText = [string]$req.Arg }

    if (-not (Add-HiveConsumedNonce -Nonce $nonce -StateFile $stateFile)) {
      Write-HiveAdminAudit $nonce $verb $argText 'ignored' 'nonce already handled'
      return $true
    }

    Write-HiveAdminAudit $nonce $verb $argText 'started' ''
    $detail = Invoke-HiveAdminVerb -Verb $req.Verb -Arg $req.Arg
    Write-HiveAdminAudit $nonce $verb $argText 'ok' ([string]$detail)
    return $true
  } catch {
    Write-HiveAdminAudit $nonce $verb $argText 'failed' $_.Exception.Message
    return $false
  }
}

function Invoke-HiveAdminHelperMain {
  # Only system module paths; the elevated process never loads user modules.
  $env:PSModulePath = (Join-Path $PSHOME 'Modules')
  if (-not (Invoke-HiveAdminRequest)) { exit 1 }
}

# Tests load the functions above from the syntax tree and never reach this line.
if ($MyInvocation.InvocationName -ne '.') { Invoke-HiveAdminHelperMain }
