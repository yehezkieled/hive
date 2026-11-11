"""deploy/windows: the one-sitting installer and the narrow admin helper.

Nothing here runs on Windows or changes a machine. Two layers:

- Static checks that always run: the helper's allow-list and request grammar
  (parsed out of the .ps1 source and exercised against the shared case file),
  forbidden constructs in the helper, and password hygiene in the installer.
- The PowerShell test scripts under deploy/windows/tests, run through pwsh
  when it is installed (GitHub's ubuntu runners have it), plus a parse of
  every .ps1 and PSScriptAnalyzer errors when that module is present.
  Set HIVE_PWSH to point at another PowerShell (for example Windows
  PowerShell through WSL interop).
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "deploy" / "windows"
HELPER = WIN / "HiveAdminHelper.ps1"
INSTALL = WIN / "Install-HiveAnywhere.ps1"
REGISTER = WIN / "Register-HiveWsl.ps1"
CASES = json.loads((WIN / "tests" / "admin-helper-cases.json").read_text())

EXPECTED_VERBS = {
    "ping": "none",
    "restart-wsl": "none",
    "restart-tailscale": "none",
    "tailscale-up": "none",
    "restart-pc": "hour-optional",
    "pause-updates": "days-optional",
}


def _block(text: str, name: str) -> str:
    m = re.search(rf"# BEGIN {name}.*?\n(.*?)# END {name}", text, re.S)
    assert m, f"{name} block missing"
    return m.group(1)


def _allow_list() -> dict[str, str]:
    body = _block(HELPER.read_text(), "ALLOW-LIST")
    return dict(re.findall(r"^\s*'([^']+)'\s*=\s*'([^']+)'\s*$", body, re.M))


def _grammar() -> dict[str, str]:
    body = _block(HELPER.read_text(), "REQUEST-GRAMMAR")
    return dict(re.findall(r"^\$script:(\w+)\s*=\s*'?(.*?)'?\s*$", body, re.M))


def _validate(raw: bytes):
    """Python mirror of ConvertFrom-HiveAdminRequest, driven by the .ps1's own
    pattern, limits and allow-list. Returns (verb, arg) or raises ValueError."""
    g = _grammar()
    verbs = _allow_list()
    if not raw or len(raw) > int(g["HiveAdminMaxRequestBytes"]):
        raise ValueError("size")
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    if not raw or any(b < 0x20 or b > 0x7E for b in raw):
        raise ValueError("bytes")
    m = re.fullmatch(g["HiveAdminRequestPattern"].strip("^$"), raw.decode("ascii"))
    if not m:
        raise ValueError("grammar")
    _, verb, arg = m.groups()
    rule = verbs.get(verb)
    if rule is None:
        raise ValueError("verb")
    if rule == "none":
        if arg is not None:
            raise ValueError("arg")
        return verb, None
    if arg is None:
        return verb, None
    n = int(arg)
    lo, hi = {
        "hour-optional": (g["HiveAdminHourMin"], g["HiveAdminHourMax"]),
        "days-optional": (g["HiveAdminDaysMin"], g["HiveAdminDaysMax"]),
    }[rule]
    if not int(lo) <= n <= int(hi):
        raise ValueError("range")
    return verb, n


def test_allow_list_is_the_fixed_short_list():
    assert _allow_list() == EXPECTED_VERBS


def test_every_verb_has_a_fixed_handler_and_nothing_else():
    text = HELPER.read_text()
    switch = re.search(r"switch -CaseSensitive \(\$Verb\) \{(.*?)\n  \}\n\}", text, re.S)
    assert switch, "Invoke-HiveAdminVerb switch not found"
    handlers = re.findall(r"^    '([a-z-]+)' \{", switch.group(1), re.M)
    assert sorted(handlers) == sorted(EXPECTED_VERBS)
    assert "default {\n      throw 'unknown verb'" in switch.group(1)


@pytest.mark.parametrize("case", CASES["accept"], ids=lambda c: c["line"].strip())
def test_grammar_accepts(case):
    assert _validate(case["line"].encode("utf-8")) == (case["verb"], case["arg"])


@pytest.mark.parametrize("case", CASES["reject"], ids=lambda c: c["why"])
def test_grammar_rejects(case):
    with pytest.raises(ValueError):
        _validate(case["line"].encode("utf-8"))


def test_pattern_uses_ascii_digits_and_anchors():
    pattern = _grammar()["HiveAdminRequestPattern"]
    assert pattern.startswith("^") and pattern.endswith("$")
    # \d and \w match non-ASCII digits/letters in .NET; the pattern must not use them.
    assert "\\d" not in pattern and "\\w" not in pattern and "\\s" not in pattern


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
    for pat in forbidden:
        assert not re.search(pat, code, re.I), pat
    # The only call operator use with a variable is the fixed-path native runner.
    calls = re.findall(r"&\s*\$\w+", code)
    assert calls == ["& $Path"], calls
    # Native tools are resolved from system folders, never from PATH.
    assert "GetFolderPath('System')" in code and "GetFolderPath('ProgramFiles')" in code
    # The helper never writes into the user-writable request folder.
    writes = r"(Remove-Item|WriteAll|AppendAll|Set-Content|Out-File)[^\n]*request"
    assert not re.search(writes, code)


def test_helper_resets_module_path_and_reads_bounded_request():
    text = HELPER.read_text()
    assert "$env:PSModulePath = (Join-Path $PSHOME 'Modules')" in text
    assert "ReparsePoint" in text
    assert "HiveAdminMaxRequestBytes" in text


def test_installer_never_exposes_the_password():
    text = INSTALL.read_text()
    assert "Start-Transcript" not in text
    assert "ConvertFrom-SecureString" not in text
    assert "Read-Host -AsSecureString" in text
    # The plain password only ever exists inside the ProcessStartInfo argument.
    plain_uses = [line for line in text.splitlines() if "PtrToStringBSTR($bstr)" in line]
    assert len(plain_uses) == 1 and "ConvertTo-WindowsArgument" in plain_uses[0]
    for line in text.splitlines():
        if re.search(r"Write-(Host|Output|Verbose|Debug|Information|Warning)|Add-Result", line):
            # Text passed to an output call must not interpolate a secret variable.
            for text_arg in re.findall(r"(?:Write-\w+|Add-Result)\s+(.*)", line):
                assert not re.search(r"\$(secure|bstr|first|second|b1|b2|psi)\b", text_arg, re.I), (
                    line
                )
    for sink in ("Out-File", "Set-Content", "Add-Content", "Export-"):
        for line in text.splitlines():
            if sink in line:
                assert not re.search(r"\$(secure|bstr|psi)\b", line, re.I), line


def test_installer_shape():
    text = INSTALL.read_text()
    assert "SupportsShouldProcess" in text
    assert "Register-HiveWsl.ps1" in text and "LogonOnly = $true" in text
    assert "'Hive Admin Helper'" in text and "NT AUTHORITY\\SYSTEM" in text
    assert "(A;;GRGX;;;$userSid)" in text
    assert "vmIdleTimeout" in text and "instanceIdleTimeout" in text
    assert "--unattended=true" in text
    assert "AutomaticRestartSignOnConfig" in text and "LockWorkStation" in text
    assert "Restore on AC Power Loss" in text
    assert "O=Microsoft Corporation" in text
    # The Startup-folder script is moved only after the task is confirmed.
    retire = text[text.index("c2. Retire Startup-folder script' {") :]
    assert retire.index("Get-ScheduledTask") < retire.index("Move-Item")


def test_register_keepalive_has_restart_loop_and_safe_names():
    text = REGISTER.read_text()
    assert "while (`$true)" in text and "Start-Sleep -Seconds 10" in text
    assert "[switch]$LogonOnly" in text
    assert "-notmatch '^[A-Za-z0-9._-]{1,64}$'" in text


def test_sitting_doc_covers_the_steps():
    doc = (ROOT / "docs" / "anywhere-sitting.md").read_text()
    for needle in ("Install-HiveAnywhere.ps1", "/login", "21 Nov 2026", "-WhatIf", "BIOS"):
        assert needle in doc, needle


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


def _ps_path(p: Path) -> str:
    if os.environ.get("HIVE_PWSH", "").endswith(".exe"):
        return subprocess.run(
            ["wslpath", "-w", str(p)], capture_output=True, text=True, check=True
        ).stdout.strip()
    return str(p)


needs_pwsh = pytest.mark.skipif(_pwsh() is None, reason="PowerShell not installed")


@needs_pwsh
@pytest.mark.parametrize("script", ["Test-HiveAdminHelper.ps1", "Test-HiveInstall.ps1"])
def test_powershell_suite(script):
    r = _run_pwsh(["-ExecutionPolicy", "Bypass", "-File", _ps_path(WIN / "tests" / script)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "failed=0" in r.stdout


@needs_pwsh
def test_powershell_parses_and_lints():
    files = sorted(WIN.rglob("*.ps1"))
    paths = ",".join("'" + _ps_path(f).replace("'", "''") + "'" for f in files)
    script = (
        "$bad = 0; foreach ($f in @(" + paths + ")) { $t = $null; $e = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$t, [ref]$e); "
        'foreach ($x in $e) { $bad++; "${f}:$($x.Extent.StartLineNumber): $($x.Message)" } }; '
        "if (Get-Module -ListAvailable PSScriptAnalyzer) { foreach ($f in @(" + paths + ")) { "
        "foreach ($d in Invoke-ScriptAnalyzer -Path $f -Severity Error) { $bad++; "
        '"${f}:$($d.Line): $($d.RuleName) $($d.Message)" } } }; exit $bad'
    )
    r = _run_pwsh(["-Command", script])
    assert r.returncode == 0, r.stdout + r.stderr
