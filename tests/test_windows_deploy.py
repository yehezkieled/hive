"""Executable checks for deploy/windows."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "deploy" / "windows"


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
    result = _run_pwsh(["-ExecutionPolicy", "Bypass", "-File", _ps_path(WIN / "tests" / script)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "failed=0" in result.stdout


@needs_pwsh
def test_powershell_parses_and_lints():
    files = sorted(WIN.rglob("*.ps1"))
    paths = ",".join("'" + _ps_path(path).replace("'", "''") + "'" for path in files)
    script = (
        "$bad = 0; foreach ($f in @(" + paths + ")) { $t = $null; $e = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$t, [ref]$e); "
        'foreach ($x in $e) { $bad++; "${f}:$($x.Extent.StartLineNumber): $($x.Message)" } }; '
        "if (Get-Module -ListAvailable PSScriptAnalyzer) { foreach ($f in @(" + paths + ")) { "
        "foreach ($d in Invoke-ScriptAnalyzer -Path $f -Severity Error) { $bad++; "
        '"${f}:$($d.Line): $($d.RuleName) $($d.Message)" } } }; exit $bad'
    )
    result = _run_pwsh(["-Command", script])
    assert result.returncode == 0, result.stdout + result.stderr
