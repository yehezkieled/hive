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

$wanted = @('ConvertTo-WindowsArgument', 'Merge-IniLines')
foreach ($stmt in $ast.EndBlock.Statements) {
  if ($stmt -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $wanted -contains $stmt.Name) {
    . ([scriptblock]::Create($stmt.Extent.Text))
  }
}

$script:failures = 0
$script:passes = 0
function Assert-Equal($Expected, $Actual, [string]$Message) {
  if ([string]$Expected -ceq [string]$Actual) { $script:passes++ }
  else { $script:failures++; Write-Output "FAIL: $Message`n  expected: $Expected`n  actual:   $Actual" }
}

# CommandLineToArgvW quoting (what Autologon64.exe receives as one argument).
Assert-Equal 'hezki' (ConvertTo-WindowsArgument 'hezki') 'plain word stays bare'
Assert-Equal '""' (ConvertTo-WindowsArgument '') 'empty is quoted'
Assert-Equal '"a b"' (ConvertTo-WindowsArgument 'a b') 'space is quoted'
Assert-Equal '"a\"b"' (ConvertTo-WindowsArgument 'a"b') 'quote is escaped'
Assert-Equal '"a\\\"b c"' (ConvertTo-WindowsArgument 'a\"b c') 'backslash before quote is doubled'
Assert-Equal '"a b\\"' (ConvertTo-WindowsArgument 'a b\') 'trailing backslash is doubled when quoted'
Assert-Equal 'a\b' (ConvertTo-WindowsArgument 'a\b') 'inner backslash without quote or space stays'
Assert-Equal "`"tab`there`"" (ConvertTo-WindowsArgument "tab`there") 'tab is quoted'

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
