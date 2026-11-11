<#
.SYNOPSIS
  Plain-assert tests for HiveAdminHelper.ps1's request validation. No Pester.

.DESCRIPTION
  Loads only the helper's function definitions and its allow-list / grammar
  assignments from the syntax tree, so the helper's main entry point never
  runs and nothing on the machine is touched. Runs on pwsh (Linux CI) and
  Windows PowerShell 5.1. Exit code 0 = all passed.

  Run: pwsh -NoProfile -File deploy/windows/tests/Test-HiveAdminHelper.ps1
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$helperPath = Join-Path (Split-Path -Parent $here) 'HiveAdminHelper.ps1'
$casesPath = Join-Path $here 'admin-helper-cases.json'

$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($helperPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "parse errors in helper: $($errors | ForEach-Object { $_.Message })" }

foreach ($stmt in $ast.EndBlock.Statements) {
  $isFunction = $stmt -is [System.Management.Automation.Language.FunctionDefinitionAst]
  $isGrammar = ($stmt -is [System.Management.Automation.Language.AssignmentStatementAst]) -and
    ($stmt.Left.Extent.Text -like '$script:HiveAdmin*')
  if ($isFunction -or $isGrammar) { . ([scriptblock]::Create($stmt.Extent.Text)) }
}

$script:failures = 0
$script:passes = 0
function Assert-True([bool]$Condition, [string]$Message) {
  if ($Condition) { $script:passes++ } else { $script:failures++; Write-Output "FAIL: $Message" }
}

$cases = Get-Content -LiteralPath $casesPath -Raw | ConvertFrom-Json
$utf8 = New-Object System.Text.UTF8Encoding($false)

foreach ($c in $cases.accept) {
  $label = $c.line -replace "`r", '\r' -replace "`n", '\n'
  try {
    $r = ConvertFrom-HiveAdminRequest -Bytes $utf8.GetBytes($c.line)
    Assert-True ($r.Verb -ceq $c.verb) "accept '$label': verb '$($r.Verb)' != '$($c.verb)'"
    if ($null -eq $c.arg) {
      Assert-True ($null -eq $r.Arg) "accept '$label': arg should be null, got '$($r.Arg)'"
    } else {
      Assert-True (($r.Arg -is [int]) -and ($r.Arg -eq [int]$c.arg)) "accept '$label': arg '$($r.Arg)' != '$($c.arg)'"
    }
  } catch {
    Assert-True $false "accept '$label' threw: $($_.Exception.Message)"
  }
}

foreach ($c in $cases.reject) {
  try {
    $null = ConvertFrom-HiveAdminRequest -Bytes $utf8.GetBytes($c.line)
    Assert-True $false "reject ($($c.why)) was accepted"
  } catch {
    $msg = $_.Exception.Message
    Assert-True ($msg -like 'rejected: *') "reject ($($c.why)): unexpected error '$msg'"
    # The reason must not echo request text back into the audit log.
    if ($c.line.Length -ge 12) {
      Assert-True (-not $msg.Contains($c.line.Trim())) "reject ($($c.why)): reason echoes the request"
    }
  }
}

Assert-True ($null -ne (& { try { ConvertFrom-HiveAdminRequest -Bytes $null } catch { 'threw' } })) 'null bytes must throw'

# Allow-list is exactly the documented set.
$expected = @('ping', 'restart-wsl', 'restart-tailscale', 'tailscale-up', 'restart-pc', 'pause-updates')
$actual = @($script:HiveAdminVerbs.Keys)
Assert-True (($actual -join ',') -ceq ($expected -join ',')) "allow-list is '$($actual -join ',')'"

# Restart delay: next occurrence of the hour.
$now = [datetime]::new(2026, 11, 11, 22, 30, 0)
Assert-True ((Get-HiveRestartDelaySeconds -Hour 3 -Now $now) -eq 16200) 'restart at 03 from 22:30 is 4.5 h'
Assert-True ((Get-HiveRestartDelaySeconds -Hour 23 -Now $now) -eq 1800) 'restart at 23 from 22:30 is 30 min'
Assert-True ((Get-HiveRestartDelaySeconds -Hour 22 -Now $now) -eq 84600) 'restart at 22 from 22:30 is tomorrow'
$onTheHour = [datetime]::new(2026, 11, 11, 3, 0, 0)
Assert-True ((Get-HiveRestartDelaySeconds -Hour 3 -Now $onTheHour) -eq 86400) 'restart at 03 from 03:00 is tomorrow'
Assert-True ($null -ne (& { try { Get-HiveRestartDelaySeconds -Hour 24 -Now $now } catch { 'threw' } })) 'hour 24 must throw'

# Pause window.
$w = Get-HivePauseWindow -Days 7 -NowUtc ([datetime]::new(2026, 11, 11, 1, 2, 3, [DateTimeKind]::Utc))
Assert-True ($w.Start -eq '2026-11-11T01:02:03Z') "pause start '$($w.Start)'"
Assert-True ($w.End -eq '2026-11-18T01:02:03Z') "pause end '$($w.End)'"
Assert-True ($null -ne (& { try { Get-HivePauseWindow -Days 36 -NowUtc ([datetime]::UtcNow) } catch { 'threw' } })) 'days 36 must throw'

function New-TestHiveRoot {
  $root = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString('N'))
  [void](New-Item -ItemType Directory -Path (Join-Path $root 'request') -Force)
  [void](New-Item -ItemType Directory -Path (Join-Path $root 'state') -Force)
  $script:HiveAdminRoot = $root
  return $root
}
function Set-TestHiveRequest([string]$Root, [string]$Line) {
  [System.IO.File]::WriteAllText((Join-Path (Join-Path $Root 'request') 'request.txt'), $Line, [System.Text.Encoding]::ASCII)
}
function Invoke-HiveAdminVerb([string]$Verb, $Arg) {
  $script:ranVerbs += $Verb
  if ($script:failVerb) { throw 'stub failure' }
  return "stub:$Verb"
}
$root = New-TestHiveRoot
$script:ranVerbs = @()
$script:failVerb = $false
Set-TestHiveRequest $root 'Aaaaaaaa restart-pc'
Assert-True (Invoke-HiveAdminRequest) 'first request is handled'
Set-TestHiveRequest $root 'Bbbbbbbb ping'
Assert-True (Invoke-HiveAdminRequest) 'second request is handled'
Set-TestHiveRequest $root 'Aaaaaaaa restart-pc'
Assert-True (Invoke-HiveAdminRequest) 'replayed request is ignored'
Assert-True (($script:ranVerbs -join ',') -ceq 'restart-pc,ping') 'A, B, A runs only A and B once'
Assert-True (-not (Test-Path (Join-Path (Join-Path $root 'request') 'request.txt'))) 'request file is gone after handling'
Remove-Item -LiteralPath $root -Recurse -Force

$root = New-TestHiveRoot
$script:failVerb = $true
Set-TestHiveRequest $root 'Cccccccc restart-pc'
Assert-True (-not (Invoke-HiveAdminRequest)) 'action failure is reported'
Assert-True (-not (Test-Path (Join-Path (Join-Path $root 'request') 'request.txt'))) 'request file is gone after action failure'
Remove-Item -LiteralPath $root -Recurse -Force

$root = New-TestHiveRoot
$script:failVerb = $false
Set-TestHiveRequest $root 'invalid request'
Assert-True (Invoke-HiveAdminRequest) 'invalid request is rejected without a task failure'
Assert-True (-not (Test-Path (Join-Path (Join-Path $root 'request') 'request.txt'))) 'request file is gone after validation failure'
Remove-Item -LiteralPath $root -Recurse -Force

$root = New-TestHiveRoot
$stateFile = Join-Path (Join-Path $root 'state') 'consumed-nonces'
for ($i = 0; $i -lt 201; $i++) {
  $value = 'N' + $i.ToString('D7')
  $null = Add-HiveConsumedNonce -Nonce $value -StateFile $stateFile
}
$stored = @([System.IO.File]::ReadAllLines($stateFile))
Assert-True ($stored.Count -eq 200) 'nonce history is capped at 200 entries'
Assert-True ($stored[0] -ceq 'N0000001' -and $stored[-1] -ceq 'N0000200') 'nonce history retains the latest 200 entries'
Remove-Item -LiteralPath $root -Recurse -Force

Write-Output "passed=$script:passes failed=$script:failures"
if ($script:failures -gt 0) { exit 1 }
exit 0
