"""Executable checks and security guards for deploy/windows."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "deploy" / "windows"
HELPER = WIN / "HiveAdminHelper.ps1"
INSTALL = WIN / "Install-HiveAnywhere.ps1"


def test_helper_has_no_way_to_run_request_text():
    code = "\n".join(
        line for line in HELPER.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    forbidden = [
        r"Invoke-Expression",
        r"\biex\b",
        r"\[scriptblock\]::Create",
        r"NewScriptBlock",
        r"Add-Type",
        r"Start-Process",
        r"-EncodedCommand",
        r"Invoke-Command",
        r"\.InvokeScript",
        r"cmd(\.exe)?\s+/c",
        r"&\s*\$req",
        r"&\s*\$text",
    ]
    import re

    for pattern in forbidden:
        assert not re.search(pattern, code, re.I), pattern
    calls = re.findall(r"&\s*\$\w+", code)
    assert calls == ["& $Path"], calls


def test_installer_password_handling_security_guard():
    text = INSTALL.read_text()
    assert "Start-Transcript" not in text
    assert "ConvertFrom-SecureString" not in text
    import re

    plain_uses = [line for line in text.splitlines() if "PtrToStringBSTR($bstr)" in line]
    assert len(plain_uses) == 1 and "ConvertTo-WindowsArgument" in plain_uses[0]
    for line in text.splitlines():
        if re.search(r"Write-(Host|Output|Verbose|Debug|Information|Warning)|Add-Result", line):
            for text_arg in re.findall(r"(?:Write-\w+|Add-Result)\s+(.*)", line):
                assert not re.search(r"\$(secure|bstr|first|second|b1|b2|psi)\b", text_arg, re.I), line
        for sink in ("Out-File", "Set-Content", "Add-Content", "Export-"):
            if sink in line:
                assert not re.search(r"\$(secure|bstr|psi)\b", line, re.I), line


def _pwsh() -> str | None:
    return os.environ.get("HIVE_PWSH") or shutil.which("pwsh")


def _run_pwsh(args: list[str]) -> subprocess.CompletedProcess:
    exe = _pwsh()
    assert exe
    return subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", *args],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _ps_path(path: Path) -> str:
    if os.environ.get("HIVE_PWSH", "").endswith(".exe"):
        return subprocess.run(
            ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
        ).stdout.strip()
    return str(path)


needs_pwsh = pytest.mark.skipif(_pwsh() is None, reason="PowerShell not installed")


@needs_pwsh
@pytest.mark.parametrize("script", ["Test-HiveAdminHelper.ps1", "Test-HiveInstall.ps1"])
def test_powershell_suite(script):
    result = _run_pwsh(
        ["-ExecutionPolicy", "Bypass", "-File", _ps_path(WIN / "tests" / script)]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "failed=0" in result.stdout


@needs_pwsh
def test_powershell_parses_and_lints():
    files = sorted(WIN.rglob("*.ps1"))
    paths = ",".join("'" + _ps_path(path).replace("'", "''") + "'" for path in files)
    script = (
        "$bad = 0; foreach ($f in @("
        + paths
        + ")) { $t = $null; $e = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$t, [ref]$e); "
        'foreach ($x in $e) { $bad++; "${f}:$($x.Extent.StartLineNumber): $($x.Message)" } }; '
        "if (Get-Module -ListAvailable PSScriptAnalyzer) { foreach ($f in @(" + paths + ")) { "
        "foreach ($d in Invoke-ScriptAnalyzer -Path $f -Severity Error) { $bad++; "
        '"${f}:$($d.Line): $($d.RuleName) $($d.Message)" } } }; exit $bad'
    )
    result = _run_pwsh(["-Command", script])
    assert result.returncode == 0, result.stdout + result.stderr
