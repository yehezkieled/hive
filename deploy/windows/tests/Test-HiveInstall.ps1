<#
.SYNOPSIS
  Plain-assert tests for the pure helpers inside Install-HiveAnywhere.ps1.

.DESCRIPTION
  Loads only the named function definitions from the syntax tree; the
  installer's steps never run. Runs on pwsh (Linux CI) and Windows PowerShell 5.1.

  Run: pwsh -NoProfile -File deploy/windows/tests/Test-HiveInstall.ps1
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$installPath = Join-Path (Split-Path -Parent $here) 'Install-HiveAnywhere.ps1'

$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "parse errors in installer: $($errors | ForEach-Object { $_.Message })" }

$wanted = @(
  'ConvertTo-WindowsArgument', 'Merge-IniLines', 'Get-HiveAccountSid',
  'Get-TaskFingerprint',
  'New-HiveAutologonStartInfo', 'Test-HiveRegistryValueMatches',
  'Test-HiveTaskEnabled', 'Test-HiveAutologonConfigured'
)
foreach ($stmt in $ast.EndBlock.Statements) {
  if ($stmt -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $wanted -contains $stmt.Name) {
    . ([scriptblock]::Create($stmt.Extent.Text))
  }
}

$registerPath = Join-Path (Split-Path -Parent $here) 'Register-HiveWsl.ps1'
$registerAst = [System.Management.Automation.Language.Parser]::ParseFile($registerPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "parse errors in keepalive script: $($errors | ForEach-Object { $_.Message })" }
foreach ($stmt in $registerAst.EndBlock.Statements) {
  if ($stmt -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $stmt.Name -in @('Get-HiveAccountSid', 'Test-HiveTaskAccountMatches', 'Get-HiveTriggerFingerprint', 'Test-HiveKeepaliveTaskEnabled')) {
    . ([scriptblock]::Create($stmt.Extent.Text))
  }
}

$script:testAccountSids = @{
  'PC\captain' = 'S-1-5-21-10-20-30-1001'
  'captain' = 'S-1-5-21-10-20-30-1001'
  'PC\other' = 'S-1-5-21-10-20-30-1002'
  'PC\hezki' = 'S-1-5-21-10-20-30-2001'
  'hezki' = 'S-1-5-21-10-20-30-2001'
  'OTHER\hezki' = 'S-1-5-21-10-20-30-2002'
}
$testAccountResolver = { param($Account) $script:testAccountSids[$Account] }

$script:failures = 0
$script:passes = 0
function Assert-Equal($Expected, $Actual, [string]$Message) {
  if ([string]$Expected -ceq [string]$Actual) { $script:passes++ }
  else { $script:failures++; Write-Output "FAIL: $Message`n  expected: $Expected`n  actual:   $Actual" }
}

Assert-Equal 'S-1-5-18' (Get-HiveAccountSid 'SYSTEM') 'SYSTEM resolves to its well-known SID'

# CommandLineToArgvW quoting (what Autologon64.exe receives as one argument).
Assert-Equal 'hezki' (ConvertTo-WindowsArgument 'hezki') 'plain word stays bare'
Assert-Equal '""' (ConvertTo-WindowsArgument '') 'empty is quoted'
Assert-Equal '"a b"' (ConvertTo-WindowsArgument 'a b') 'space is quoted'
Assert-Equal '"a\"b"' (ConvertTo-WindowsArgument 'a"b') 'quote is escaped'
Assert-Equal '"a\\\"b c"' (ConvertTo-WindowsArgument 'a\"b c') 'backslash before quote is doubled'
Assert-Equal '"a b\\"' (ConvertTo-WindowsArgument 'a b\') 'trailing backslash is doubled when quoted'
Assert-Equal 'a\b' (ConvertTo-WindowsArgument 'a\b') 'inner backslash without quote or space stays'
Assert-Equal "`"tab`there`"" (ConvertTo-WindowsArgument "tab`there") 'tab is quoted'
$fixturePassword = New-Object System.Security.SecureString
foreach ($character in 'fixture password'.ToCharArray()) { $fixturePassword.AppendChar($character) }
$fixturePassword.MakeReadOnly()
$startInfoOutput = @(& { New-HiveAutologonStartInfo 'autologon.exe' 'captain' 'PC' $fixturePassword } *>&1)
$fixturePassword.Dispose()
Assert-Equal 1 $startInfoOutput.Count 'Autologon argument builder emits only its result object'
Assert-Equal 'System.Diagnostics.ProcessStartInfo' $startInfoOutput[0].GetType().FullName 'Autologon argument builder emits no password text'
Assert-Equal 'captain PC "fixture password" /accepteula' $startInfoOutput[0].Arguments 'password is confined to the process argument'

$trigger = [pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskLogonTrigger' }; UserId = 'PC\captain' }
$task = [pscustomobject]@{
  Actions = @([pscustomobject]@{ Execute = 'lock.exe'; Arguments = 'lock' })
  Triggers = @($trigger)
  Principal = [pscustomobject]@{ UserId = 'PC\captain'; LogonType = 'Interactive'; RunLevel = 'Limited' }
  State = 'Ready'
}
$fingerprint = Get-TaskFingerprint $task $testAccountResolver
Assert-Equal $fingerprint (Get-TaskFingerprint $task $testAccountResolver) 'same logon trigger and user keep task unchanged'
$task.Triggers = @([pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskLogonTrigger' }; UserId = 'PC\other' })
Assert-Equal $false ($fingerprint -ceq (Get-TaskFingerprint $task $testAccountResolver)) 'different logon trigger user changes task fingerprint'
$task.Triggers = @([pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskBootTrigger' }; UserId = '' })
Assert-Equal $false ($fingerprint -ceq (Get-TaskFingerprint $task)) 'different trigger type changes task fingerprint'
Assert-Equal $true (Test-HiveTaskEnabled $task) 'enabled scheduled task is eligible to match'
$task.State = 'Disabled'
Assert-Equal $false (Test-HiveTaskEnabled $task) 'disabled scheduled task does not match'

$identityTask = [pscustomobject]@{
  Actions = @([pscustomobject]@{ Execute = 'keepalive.exe'; Arguments = 'loop' })
  Triggers = @([pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskLogonTrigger' }; UserId = 'PC\hezki' })
  Principal = [pscustomobject]@{ UserId = 'PC\hezki'; LogonType = 'Interactive'; RunLevel = 'Limited' }
}
$identityFingerprint = Get-TaskFingerprint $identityTask $testAccountResolver
$identityTask.Principal.UserId = 'OTHER\hezki'
Assert-Equal $false ($identityFingerprint -ceq (Get-TaskFingerprint $identityTask $testAccountResolver)) 'same username under a different domain does not match task principal'
$identityTask.Principal.UserId = 'hezki'
Assert-Equal $identityFingerprint (Get-TaskFingerprint $identityTask $testAccountResolver) 'same account as an unqualified name matches task principal'
$identityTask.Principal.UserId = 'S-1-5-21-10-20-30-2001'
Assert-Equal $identityFingerprint (Get-TaskFingerprint $identityTask $testAccountResolver) 'same account as a SID matches task principal'
$identityTask.Principal.UserId = 'PC\hezki'
$identityTask.Triggers[0].UserId = 'OTHER\hezki'
Assert-Equal $false ($identityFingerprint -ceq (Get-TaskFingerprint $identityTask $testAccountResolver)) 'same username under a different domain does not match logon trigger'
$identityTask.Triggers[0].UserId = 'hezki'
Assert-Equal $identityFingerprint (Get-TaskFingerprint $identityTask $testAccountResolver) 'same account as an unqualified name matches logon trigger'
$identityTask.Triggers[0].UserId = 'S-1-5-21-10-20-30-2001'
Assert-Equal $identityFingerprint (Get-TaskFingerprint $identityTask $testAccountResolver) 'same account as a SID matches logon trigger'
Assert-Equal $true (Test-HiveTaskAccountMatches 'PC\hezki' 'hezki' $testAccountResolver) 'qualified and unqualified same-account names match'
Assert-Equal $false (Test-HiveTaskAccountMatches 'OTHER\hezki' 'PC\hezki' $testAccountResolver) 'same username in another domain does not match'

$logon = [pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskLogonTrigger' }; UserId = 'PC\captain' }
$otherLogon = [pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskLogonTrigger' }; UserId = 'PC\other' }
$startup = [pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskBootTrigger' }; UserId = '' }
Assert-Equal (Get-HiveTriggerFingerprint @($logon) $testAccountResolver) (Get-HiveTriggerFingerprint @($logon) $testAccountResolver) 'same keepalive logon trigger matches'
Assert-Equal $false ((Get-HiveTriggerFingerprint @($logon) $testAccountResolver) -ceq (Get-HiveTriggerFingerprint @($otherLogon) $testAccountResolver)) 'different keepalive logon user does not match'
Assert-Equal $false ((Get-HiveTriggerFingerprint @($logon)) -ceq (Get-HiveTriggerFingerprint @($startup))) 'different keepalive trigger type does not match'
Assert-Equal $true (Test-HiveKeepaliveTaskEnabled ([pscustomobject]@{ State = 'Ready' })) 'enabled keepalive task matches'
Assert-Equal $false (Test-HiveKeepaliveTaskEnabled ([pscustomobject]@{ State = 'Disabled' })) 'disabled keepalive task is re-registered'

Assert-Equal $true (Test-HiveRegistryValueMatches 0 0 'DWord' 'DWord') 'matching registry value and kind are unchanged'
Assert-Equal $false (Test-HiveRegistryValueMatches '0' 0 'String' 'DWord') 'matching registry text with wrong kind is changed'
Assert-Equal $false (Test-HiveRegistryValueMatches 1 0 'DWord' 'DWord') 'different registry data is changed'

$winlogon = [pscustomobject]@{ AutoAdminLogon = '1'; DefaultUserName = 'captain'; DefaultDomainName = 'PC' }
Assert-Equal $true (Test-HiveAutologonConfigured $winlogon 'captain' 'PC') 'matching Autologon account is configured'
Assert-Equal $false (Test-HiveAutologonConfigured $winlogon 'captain' 'OTHER') 'different Autologon domain is not configured'

$want = [ordered]@{ 'wsl2' = [ordered]@{ 'vmIdleTimeout' = '-1' }; 'general' = [ordered]@{ 'instanceIdleTimeout' = '-1' } }

# No file yet.
$r = Merge-IniLines @() $want
Assert-Equal "[wsl2]|vmIdleTimeout=-1||[general]|instanceIdleTimeout=-1" ($r.Lines -join '|') 'new file'
Assert-Equal 2 @($r.Changed).Count 'new file changes both'

# Already right: nothing changes (idempotent).
$again = Merge-IniLines $r.Lines $want
Assert-Equal 0 @($again.Changed).Count 'second merge changes nothing'
Assert-Equal ($r.Lines -join '|') ($again.Lines -join '|') 'second merge keeps lines'

# Existing file: other keys kept, value updated in place, missing key appended to its section.
$existing = @('# mine', '[wsl2]', 'memory=32GB', 'vmIdleTimeout = 60000', '', '[experimental]', 'autoMemoryReclaim=gradual')
$r = Merge-IniLines $existing $want
Assert-Equal '# mine|[wsl2]|memory=32GB|vmIdleTimeout=-1||[experimental]|autoMemoryReclaim=gradual||[general]|instanceIdleTimeout=-1' ($r.Lines -join '|') 'update and append'

$existing = @('[General]', 'foo=1', '', '[wsl2]', 'swap=0')
$r = Merge-IniLines $existing $want
Assert-Equal '[General]|foo=1|instanceIdleTimeout=-1||[wsl2]|swap=0|vmIdleTimeout=-1' ($r.Lines -join '|') 'insert into existing sections, section names case-insensitive'

# A key with the same name in another section is not touched.
$existing = @('[other]', 'vmIdleTimeout=5')
$r = Merge-IniLines $existing $want
Assert-Equal '[other]|vmIdleTimeout=5||[wsl2]|vmIdleTimeout=-1||[general]|instanceIdleTimeout=-1' ($r.Lines -join '|') 'other section untouched'

Write-Output "passed=$script:passes failed=$script:failures"
if ($script:failures -gt 0) { exit 1 }
exit 0
