"""``start.ps1``: the Windows one-command install that ends in an open chat.

cli.sh is POSIX-only, so on Windows the install start.ps1 wraps is the desktop
app's signed NSIS installer, run unchanged. The trust root is Authenticode plus
the publisher the desktop updater pins, and start.ps1 must hold that one copy
and no other. The contract tests below read the script text, so they run on
every host. The behaviour tests drive the real script under a native ``pwsh``
and skip without one. The install path is exercised by extracting the shipped
functions and stubbing the few Windows-only cmdlets (the download, the
signature check, the installer process, the registry), because no test may
download or install anything. The script itself only ever installs on Windows.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest
from installer_test_helpers import run_bounded

from conftest import requires_symlinks
from kiro_crew.release_channel import CHANNELS

REPO_ROOT = Path(__file__).resolve().parents[1]
START_PS1 = REPO_ROOT / "start.ps1"
START_SH = REPO_ROOT / "start.sh"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-installer.yml"

BODY = START_PS1.read_text(encoding="utf-8")
PUBLISHER = "Amazon Web Services, Inc."
BUNDLED_CLI = r"resources\backend-dist\kirocrew-backend\bin\kirocrew.cmd"

posix_only = pytest.mark.skipif(
    os.name == "nt",
    reason="stubs a kirocrew as a /bin/sh script; the Windows run is the install smoke",
)


def _code_lines() -> list[str]:
    """The script's lines minus whole-line comments, so prose cannot match."""
    return [line for line in BODY.splitlines() if not line.lstrip().startswith("#")]


def _function(name: str) -> str:
    start = BODY.index(f"function {name}")
    return BODY[start : BODY.index("\n}", start) + 2]


# ── contract: read from the text, every host ─────────────────────────


def test_the_script_is_ascii() -> None:
    """Windows PowerShell 5.1 reads a BOM-less .ps1 in the ANSI code page, and
    ``irm`` decodes by the response charset, so one non-ASCII byte can turn
    into a parse error on some machines and not others."""
    assert START_PS1.read_bytes().isascii()


def test_the_publisher_is_the_one_the_updater_and_the_publish_gate_pin() -> None:
    """One trust root: the signer start.ps1 demands is the one the desktop
    updater demands of every update and the one publish-windows.yml verifies
    before the bytes go live. A drift here refuses every Windows install."""
    package = json.loads(
        (REPO_ROOT / "website" / "electron" / "package.json").read_text(encoding="utf-8")
    )
    assert package["build"]["win"]["signtoolOptions"]["publisherName"] == [PUBLISHER]
    publish = (REPO_ROOT / ".github" / "workflows" / "publish-windows.yml").read_text(
        encoding="utf-8"
    )
    assert f'EXPECT_SUBJECT_CN: "{PUBLISHER}"' in publish
    assert re.findall(r'\$publisher = "([^"]*)"', BODY) == [PUBLISHER]


def test_the_signature_is_checked_before_the_one_thing_it_runs() -> None:
    code = "\n".join(_code_lines())
    assert code.count("Start-Process") == 1, "start.ps1 runs exactly one downloaded file"
    run = code.index("Start-Process")
    assert code.index("Get-AuthenticodeSignature") < run
    assert code.index("SignatureStatus]::Valid") < run
    assert code.index("$signer -cne $Publisher") < run
    assert '@("/S", "/currentuser")' in code
    # The only other process start is the bundled kiro-cli's `--version` probe,
    # of a file inside the installed app, never a download.
    assert code.count("[System.Diagnostics.Process]::Start(") == 1
    probe = code.index("[System.Diagnostics.Process]::Start(")
    assert code.rindex("function ", 0, probe) == code.index("function Get-KsBundledKiroEnv")
    assert code.index('$exe = Join-Path $dir "kiro-cli.exe"') < probe


def test_the_channels_are_the_release_channels() -> None:
    found = re.search(r"\$channels = @\(([^)]*)\)", BODY)
    assert found, "no channel list in start.ps1"
    assert tuple(re.findall(r'"([^"]+)"', found.group(1))) == CHANNELS


def test_the_installer_url_is_the_published_object() -> None:
    assert '"$base/desktop/$Channel/$release/KiroCrew-Setup.exe"' in BODY
    layout = (REPO_ROOT / "docs" / "build" / "release.md").read_text(encoding="utf-8")
    assert "desktop/<channel>/latest/KiroCrew-Setup.exe" in layout
    assert "desktop/<channel>/<version>/KiroCrew-Setup.exe" in layout
    # The same CDN start.sh fetches cli.sh from.
    default = "https://download.crew.kiro.dev"
    assert f'$base = "{default}"' in BODY
    assert f'_KS_BASE="${{KIROCREW_CDN_BASE:-{default}}}"' in START_SH.read_text(encoding="utf-8")


def test_the_bundled_cli_is_where_the_desktop_build_puts_it() -> None:
    assert BUNDLED_CLI in BODY
    build = (REPO_ROOT / "packaging" / "build-desktop.sh").read_text(encoding="utf-8")
    assert '"$ELECTRON_DIR/backend-dist/kirocrew-backend"' in build
    assert '> "$out/bin/kirocrew.cmd"' in build
    smoke = (REPO_ROOT / "scripts" / "smoke-windows-install.ps1").read_text(encoding="utf-8")
    assert r'Join-Path $installLocation "resources\backend-dist\kirocrew-backend"' in smoke
    assert r'Join-Path $backendRoot "bin\kirocrew.cmd"' in smoke


def test_nothing_runs_until_the_last_line() -> None:
    """Under ``irm | iex`` a cut-short download is parsed as it arrived. Every
    top-level statement except the last line is a function definition or the
    param block, so a prefix defines functions and stops."""
    lines = [line for line in _code_lines() if line.strip()]
    assert lines[-1].startswith("Invoke-KsStart "), "the call must be the last line"
    in_here_string = False
    for line in lines[:-1]:
        if in_here_string:
            in_here_string = line != "'@"
            continue
        if line.rstrip().endswith("@'"):
            in_here_string = True
        if line[0].isspace():
            continue
        assert re.match(r"(function \S|\[CmdletBinding|param\(|\)|\})", line), line


def test_it_exits_only_when_run_as_a_file() -> None:
    """``exit`` under ``irm | iex`` closes the user's own PowerShell window."""
    exits = [
        line.strip() for line in _code_lines() if re.search(r"(?:^|[{;])\s*exit\b", line.strip())
    ]
    assert exits == ["if ($FromFile) { exit $code }"]


def test_the_marker_is_written_only_after_a_successful_install() -> None:
    success = BODY.index("if ($installed -ne 0) { return $installed }")
    assert success < BODY.index("Write-KsMarker -DataHome $dataHome")


def test_kirocrew_start_takes_the_flags_it_is_handed() -> None:
    cli = (REPO_ROOT / "src" / "kiro_crew" / "cli.py").read_text(encoding="utf-8")
    for flag in re.findall(r'\$startArgs \+= "([^"]+)"', BODY):
        assert re.search(rf'start_parser\.add_argument\(\s*"{flag}"', cli), flag


def test_the_help_names_every_parameter() -> None:
    params = re.search(r"\nparam\((.*?)\n\)", BODY, re.DOTALL)
    assert params
    usage = _function("Write-KsUsage")
    for name in re.findall(r"\[(?:string|switch)\]\$(\w+)", params.group(1)):
        if name != "Help":
            assert f"-{name}" in usage, name


def test_the_publish_workflow_ships_it_after_cli_sh_is_live() -> None:
    """start.ps1 holds no trust of its own, but it is the documented Windows
    one-liner, so it is published and read back exactly as start.sh is."""
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "      - 'start.ps1'\n" in workflow, "a start.ps1 change must trigger a publish"
    assert "--key start.ps1" in workflow
    assert workflow.index("Verify the published installer is live") < workflow.index(
        "--key start.ps1"
    )
    assert workflow.index("--key start.ps1") < workflow.index(
        "Verify the published Windows start script is live"
    )
    assert "pwsh -NoProfile -NonInteractive -File start.ps1 -Help" in workflow


# ── behaviour: the real script under a native pwsh ───────────────────


def _native_pwsh() -> str | None:
    """A PowerShell that is itself the binary, not a version-manager shim.

    A shim resolves its toolchain through HOME, which these runs isolate, so it
    would try to provision one. Windows has no shims here and needs PATHEXT.
    """
    for name in ("pwsh", "powershell"):
        if os.name == "nt":
            found = shutil.which(name)
            if found:
                return found
            continue
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            try:
                resolved = (Path(entry) / name).resolve(strict=True)
                head = resolved.read_bytes()[:2]
            except OSError:
                continue
            if resolved.name == name and head != b"#!" and os.access(resolved, os.X_OK):
                return str(resolved)
    return None


PWSH = _native_pwsh()
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="no native PowerShell binary on PATH")


class _Pwsh:
    """An isolated HOME, data home, TMP and PATH for one PowerShell run."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.data_home = tmp_path / "data-home"
        self.tmp = tmp_path / "tmp"
        self.bin = tmp_path / "bin"
        self.markers = tmp_path / "markers"
        for directory in (self.home, self.tmp, self.bin, self.markers):
            directory.mkdir(parents=True)

    def fake_kirocrew(self, path: Path, exit_code: int = 0) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "#!/bin/sh\n"
            f'for a in "$@"; do printf "%s\\n" "$a"; done > "{self.markers}/kirocrew-argv"\n'
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    @property
    def kirocrew_argv(self) -> list[str] | None:
        path = self.markers / "kirocrew-argv"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else None

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": os.pathsep.join([str(self.bin), os.environ.get("PATH", "")]),
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CACHE_HOME": str(self.home / "cache"),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_DATA_HOME": str(self.home / "data"),
            "TMPDIR": str(self.tmp),
            "TMP": str(self.tmp),
            "TEMP": str(self.tmp),
            "KIROCREW_HOME": str(self.data_home),
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        }
        for name in ("SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC", "PSModulePath"):
            if name in os.environ:
                env[name] = os.environ[name]
        env.update(extra)
        return env

    def run_file(self, script: Path, *args: str, **env: str):
        assert PWSH
        return run_bounded(
            [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script), *args],
            self.env(**env),
            timeout=120,
            cwd=str(self.root),
        )

    def run_command(self, command: str, **env: str):
        assert PWSH
        return run_bounded(
            [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
            self.env(**env),
            timeout=120,
            cwd=str(self.root),
        )


@needs_pwsh
def test_the_script_parses(tmp_path: Path) -> None:
    result = _Pwsh(tmp_path).run_command(
        "$errors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{START_PS1}', [ref]$null, [ref]$errors); "
        "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    assert result.returncode == 0, result.stdout + result.stderr


@needs_pwsh
def test_help_prints_the_one_liner(tmp_path: Path) -> None:
    result = _Pwsh(tmp_path).run_file(START_PS1, "-Help")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "irm https://download.crew.kiro.dev/start.ps1 | iex" in result.stdout
    assert "-SkipInstall" in result.stdout


@needs_pwsh
@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["-DefinitelyNotAFlag"], id="unknown"),
        pytest.param(["insider"], id="positional"),
        pytest.param(["--no-browser"], id="posix-spelling"),
    ],
)
def test_an_argument_it_does_not_declare_is_refused(tmp_path: Path, args: list[str]) -> None:
    harness = _Pwsh(tmp_path)
    result = harness.run_file(START_PS1, *args, "-Cdn", "https://127.0.0.1:9")
    assert result.returncode != 0
    assert "parameter" in (result.stdout + result.stderr).lower()
    assert not harness.data_home.exists()


@needs_pwsh
@pytest.mark.parametrize(
    "args, message",
    [
        pytest.param(["-Channel", "beta"], "unknown channel 'beta'", id="channel"),
        pytest.param(["-Version", "1.2/../x"], "-Version wants X.Y.Z", id="version"),
        pytest.param(["-Cdn", "http://cdn.example.test"], "must be an https:// URL", id="cdn"),
    ],
)
def test_a_bad_value_is_refused_before_any_download(
    tmp_path: Path, args: list[str], message: str
) -> None:
    harness = _Pwsh(tmp_path)
    result = harness.run_file(START_PS1, *args)
    assert result.returncode == 2
    assert message in result.stderr
    assert not harness.data_home.exists()


@needs_pwsh
@posix_only
def test_skip_install_runs_the_kirocrew_on_path_with_its_flags(tmp_path: Path) -> None:
    harness = _Pwsh(tmp_path)
    harness.fake_kirocrew(harness.bin / "kirocrew", exit_code=5)
    result = harness.run_file(START_PS1, "-SkipInstall", "-NoBrowser", "-Foreground")
    assert result.returncode == 5, result.stdout + result.stderr
    assert harness.kirocrew_argv == ["start", "--no-browser", "--foreground"]
    # Nothing was installed here, so this run is not the install's origin.
    assert not (harness.data_home / "install-origin").exists()


@needs_pwsh
@posix_only
def test_under_iex_a_refusal_leaves_the_session_running(tmp_path: Path) -> None:
    result = _Pwsh(tmp_path).run_command(
        f"Get-Content -Raw -LiteralPath '{START_PS1}' | Invoke-Expression; "
        'Write-Output "still-here $LASTEXITCODE"',
        KIROCREW_CHANNEL="beta",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "still-here 2" in result.stdout


@needs_pwsh
@posix_only
@pytest.mark.parametrize(
    "cut",
    [
        pytest.param(lambda text: len(text) // 4, id="first-quarter"),
        pytest.param(lambda text: len(text) // 2, id="half"),
        pytest.param(lambda text: text.index("function Invoke-KsStart"), id="before-main"),
        pytest.param(lambda text: text.rindex("\nInvoke-KsStart "), id="before-the-last-line"),
    ],
)
def test_a_truncated_download_runs_nothing(tmp_path: Path, cut) -> None:
    harness = _Pwsh(tmp_path)
    harness.fake_kirocrew(harness.bin / "kirocrew")
    truncated = tmp_path / "start-truncated.ps1"
    truncated.write_text(BODY[: cut(BODY)], encoding="utf-8")
    harness.run_file(truncated, "-SkipInstall", "-Cdn", "https://127.0.0.1:9")
    assert harness.kirocrew_argv is None
    assert not harness.data_home.exists()


# The Windows-only cmdlets the install path calls, replaced for a POSIX run.
# Functions outrank cmdlets in PowerShell's command lookup, so defining these
# after the shipped functions reroutes exactly those calls and nothing else.
_INSTALL_STUBS = r"""
$script:calls = @()
$script:listed = 0
function Test-KsWindows { return $true }
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing)
    $script:calls += "download $Uri"
    $script:downloaded = $OutFile
    Set-Content -LiteralPath $OutFile -Value "installer bytes"
}
function Get-AuthenticodeSignature {
    param($LiteralPath)
    $cert = [pscustomobject]@{}
    $cert | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value { param($type, $issuer) $env:KS_SIGNER }
    [pscustomobject]@{
        Status = [System.Management.Automation.SignatureStatus]::$($env:KS_STATUS)
        StatusMessage = "stubbed"
        SignerCertificate = $cert
    }
}
function Start-Process {
    param($FilePath, $ArgumentList, [switch]$PassThru)
    $script:calls += "run " + ($ArgumentList -join " ")
    $process = [pscustomobject]@{ Id = 1; Handle = 1; ExitCode = [int]$env:KS_INSTALLER_EXIT }
    $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($ms) $true }
    return $process
}
function Get-KsDesktopInstalls {
    $script:listed += 1
    if ($script:listed -eq 1) { return @() }
    return @([pscustomobject]@{ Key = "guid"; Version = "1.2.3"; Cli = $env:KS_BUNDLED_CLI })
}
"""


def _stubbed_install_script(tmp_path: Path) -> Path:
    script = tmp_path / "stubbed-start.ps1"
    script.write_text(
        BODY[: BODY.rindex("\nInvoke-KsStart ")]
        + "\n"
        + _INSTALL_STUBS
        + "Invoke-KsStart -Channel $Channel -Version $Version -Cdn $Cdn"
        " -NoBrowser $NoBrowser.IsPresent -Foreground $Foreground.IsPresent"
        " -SkipInstall $SkipInstall.IsPresent -Help $Help.IsPresent -FromFile $false\n"
        '$script:calls | ForEach-Object { Write-Output "CALL $_" }\n'
        'Write-Output "LEFT $(Test-Path -LiteralPath $script:downloaded)"\n'
        'Write-Output "RC $LASTEXITCODE"\n',
        encoding="utf-8",
    )
    return script


def _calls(stdout: str) -> list[str]:
    return [line[len("CALL ") :] for line in stdout.splitlines() if line.startswith("CALL ")]


@needs_pwsh
@posix_only
def test_a_verified_install_records_start_and_runs_the_bundled_cli(tmp_path: Path) -> None:
    harness = _Pwsh(tmp_path)
    cli = harness.fake_kirocrew(tmp_path / "install" / "bin" / "kirocrew.cmd", exit_code=4)
    result = harness.run_file(
        _stubbed_install_script(tmp_path),
        "-Channel",
        "Insider",
        "-Version",
        "0.7.0",
        "-Cdn",
        "https://cdn.example.test/",
        "-NoBrowser",
        KS_STATUS="Valid",
        KS_SIGNER=PUBLISHER,
        KS_INSTALLER_EXIT="0",
        KS_BUNDLED_CLI=str(cli),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(result.stdout) == [
        "download https://cdn.example.test/desktop/insider/0.7.0/KiroCrew-Setup.exe",
        "run /S /currentuser",
    ]
    assert "LEFT False" in result.stdout, "the downloaded installer must be removed"
    assert "RC 4" in result.stdout, "kirocrew start's exit status is the script's"
    assert harness.kirocrew_argv == ["start", "--no-browser"]
    assert (harness.data_home / "install-origin").read_bytes() == b"start\n"


@needs_pwsh
@posix_only
@pytest.mark.parametrize(
    "status, signer",
    [
        pytest.param("NotSigned", PUBLISHER, id="unsigned"),
        pytest.param("HashMismatch", PUBLISHER, id="tampered"),
        pytest.param("UnknownError", PUBLISHER, id="unverifiable"),
        pytest.param("Valid", "Amazon Web Services", id="another-publisher"),
        pytest.param("Valid", PUBLISHER.lower(), id="publisher-case"),
    ],
)
def test_an_installer_without_the_pinned_valid_signature_never_runs(
    tmp_path: Path, status: str, signer: str
) -> None:
    harness = _Pwsh(tmp_path)
    cli = harness.fake_kirocrew(tmp_path / "install" / "bin" / "kirocrew.cmd")
    result = harness.run_file(
        _stubbed_install_script(tmp_path),
        KS_STATUS=status,
        KS_SIGNER=signer,
        KS_INSTALLER_EXIT="0",
        KS_BUNDLED_CLI=str(cli),
    )
    assert _calls(result.stdout) == [
        "download https://download.crew.kiro.dev/desktop/stable/latest/KiroCrew-Setup.exe"
    ]
    assert "refusing to run it" in result.stderr
    assert "RC 1" in result.stdout
    assert "LEFT False" in result.stdout
    assert harness.kirocrew_argv is None
    assert not harness.data_home.exists()


@needs_pwsh
@posix_only
def test_a_failed_install_starts_nothing_and_keeps_its_status(tmp_path: Path) -> None:
    harness = _Pwsh(tmp_path)
    cli = harness.fake_kirocrew(tmp_path / "install" / "bin" / "kirocrew.cmd")
    result = harness.run_file(
        _stubbed_install_script(tmp_path),
        KS_STATUS="Valid",
        KS_SIGNER=PUBLISHER,
        KS_INSTALLER_EXIT="7",
        KS_BUNDLED_CLI=str(cli),
    )
    assert "failed with exit status 7" in result.stderr
    assert "RC 7" in result.stdout
    assert harness.kirocrew_argv is None
    assert not harness.data_home.exists()


@needs_pwsh
@posix_only
def test_desktop_installs_are_found_by_their_bundled_cli(tmp_path: Path) -> None:
    """The registry walk, against stubbed keys: the install-info key beside
    each Uninstall entry gives the InstallLocation, and only a location that
    holds the bundled CLI counts, whatever the product is called."""
    harness = _Pwsh(tmp_path)
    ours = tmp_path / "Programs" / "kiro-crew"
    cli = ours.joinpath(*BUNDLED_CLI.split("\\"))
    cli.parent.mkdir(parents=True)
    cli.write_text("@echo off\n", encoding="utf-8")
    (tmp_path / "Programs" / "other-app").mkdir()
    script = tmp_path / "installs.ps1"
    script.write_text(
        "Set-StrictMode -Version 3.0\n" + _function("Get-KsDesktopInstalls") + "\n" + r"""
$uninstall = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
function Get-ChildItem {
    param($LiteralPath, $ErrorAction)
    foreach ($leaf in @("kiro-guid", "other-guid", "no-info-key")) {
        [pscustomobject]@{ PSChildName = $leaf; PSPath = "$uninstall\$leaf" }
    }
}
function Get-ItemProperty {
    param($LiteralPath, $ErrorAction)
    switch ($LiteralPath) {
        "HKCU:\Software\kiro-guid" { return [pscustomobject]@{ InstallLocation = $env:KS_OURS } }
        "HKCU:\Software\other-guid" { return [pscustomobject]@{ InstallLocation = $env:KS_OTHER } }
        "$uninstall\kiro-guid" { return [pscustomobject]@{ DisplayVersion = "0.7.0" } }
        default { return $null }
    }
}
Get-KsDesktopInstalls | ForEach-Object { Write-Output "$($_.Key)|$($_.Version)|$($_.Cli)" }
""",
        encoding="utf-8",
    )
    result = harness.run_file(
        script, KS_OURS=str(ours), KS_OTHER=str(tmp_path / "Programs" / "other-app")
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # PowerShell turns the backslashes into this platform's separator.
    assert result.stdout.splitlines() == [f"kiro-guid|0.7.0|{cli}"]


@needs_pwsh
@posix_only
class TestMarkerWrite:
    """``Write-KsMarker`` extracted and run: the data home is agent-writable."""

    def _write(self, harness: _Pwsh, data_home: Path):
        script = harness.root / "marker.ps1"
        script.write_text(
            "Set-StrictMode -Version 3.0\n"
            f"{_function('Write-KsError')}\n{_function('Write-KsMarker')}\n"
            f"Write-KsMarker -DataHome '{data_home}' -Name 'install-origin' -Value 'start'\n",
            encoding="utf-8",
        )
        return harness.run_file(script)

    def test_writes_the_exact_bytes_start_sh_writes(self, tmp_path: Path) -> None:
        harness = _Pwsh(tmp_path)
        nested = tmp_path / "fresh" / "nested"
        assert self._write(harness, nested).returncode == 0
        assert (nested / "install-origin").read_bytes() == b"start\n"
        assert [p.name for p in nested.iterdir()] == ["install-origin"]

    @requires_symlinks
    @pytest.mark.parametrize("kind", ["file", "directory"])
    def test_a_planted_link_is_replaced_not_followed(self, tmp_path: Path, kind: str) -> None:
        harness = _Pwsh(tmp_path)
        victim = tmp_path / "victim"
        if kind == "file":
            victim.write_text("untouched\n", encoding="utf-8")
        else:
            victim.mkdir()
            (victim / "keep").write_text("untouched\n", encoding="utf-8")
        harness.data_home.mkdir()
        (harness.data_home / "install-origin").symlink_to(victim)
        assert self._write(harness, harness.data_home).returncode == 0
        marker = harness.data_home / "install-origin"
        assert not marker.is_symlink()
        assert marker.read_bytes() == b"start\n"
        if kind == "file":
            assert victim.read_text(encoding="utf-8") == "untouched\n"
        else:
            assert [p.name for p in victim.iterdir()] == ["keep"]

    def test_a_directory_at_the_marker_is_refused(self, tmp_path: Path) -> None:
        harness = _Pwsh(tmp_path)
        (harness.data_home / "install-origin").mkdir(parents=True)
        result = self._write(harness, harness.data_home)
        assert result.returncode == 0, "bookkeeping only: the start continues"
        assert "a directory occupies" in result.stderr
        assert list((harness.data_home / "install-origin").iterdir()) == []


# ── the desktop app's bundled kiro-cli (RFC Q14) ─────────────────────


def _bundled_layout(harness: "_Pwsh", tmp_path: Path, *, kiro_exit: int | None) -> Path:
    """A desktop install's resources: the bundled CLI, and a kiro-cli.exe unless None."""
    resources = tmp_path / "install" / "resources" / "backend-dist"
    cli = resources / "kirocrew-backend" / "bin" / "kirocrew.cmd"
    cli.parent.mkdir(parents=True)
    cli.write_text(
        "#!/bin/sh\n"
        f'printf "%s|%s\\n" "$KIROCREW_BUNDLED_KIRO_DIR" "$KIRO_NO_AUTO_UPDATE"'
        f' > "{harness.markers}/kirocrew-env"\n',
        encoding="utf-8",
    )
    cli.chmod(0o755)
    if kiro_exit is not None:
        exe = resources / "kiro-cli" / "kiro-cli.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text(f"#!/bin/sh\nexit {kiro_exit}\n", encoding="utf-8")
        exe.chmod(0o755)
    return cli


def _run_bundled(harness: "_Pwsh", tmp_path: Path, cli: Path):
    script = tmp_path / "bundled-start.ps1"
    script.write_text(
        BODY[: BODY.rindex("\nInvoke-KsStart ")]
        + "\nfunction Resolve-KsKirocrew { param($Channel, $Version, $Cdn, $SkipInstall) "
        + f"return '{cli}' }}\n"
        + "Invoke-KsStart -Channel '' -Version '' -Cdn '' -NoBrowser $true -Foreground $false"
        " -SkipInstall $true -Help $false -FromFile $false\n"
        'Write-Output "AFTER [$env:KIROCREW_BUNDLED_KIRO_DIR]"\n',
        encoding="utf-8",
    )
    return harness.run_file(script)


@needs_pwsh
@posix_only
def test_the_bundled_kiro_cli_is_handed_to_kirocrew_start_and_put_back(tmp_path: Path) -> None:
    harness = _Pwsh(tmp_path)
    cli = _bundled_layout(harness, tmp_path, kiro_exit=0)
    result = _run_bundled(harness, tmp_path, cli)
    assert result.returncode == 0, result.stdout + result.stderr
    bundled_dir, no_update = (harness.markers / "kirocrew-env").read_text().strip().split("|")
    assert Path(bundled_dir) == tmp_path / "install" / "resources" / "backend-dist" / "kiro-cli"
    assert no_update == "1"
    # Under `irm | iex` this is the user's own session: nothing is left behind.
    assert "AFTER []" in result.stdout


@needs_pwsh
@posix_only
@pytest.mark.parametrize("kiro_exit", [None, 3], ids=["not-bundled", "does-not-run-here"])
def test_no_runnable_bundled_kiro_cli_sets_nothing(tmp_path: Path, kiro_exit) -> None:
    harness = _Pwsh(tmp_path)
    cli = _bundled_layout(harness, tmp_path, kiro_exit=kiro_exit)
    result = _run_bundled(harness, tmp_path, cli)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (harness.markers / "kirocrew-env").read_text().strip() == "|"


def test_only_the_desktop_install_s_own_cli_gets_the_bundled_copy() -> None:
    # The bundled directory is derived from the desktop CLI's own path, never
    # searched for: a kirocrew found on PATH is run exactly as before.
    assert r'$suffix = "\resources\backend-dist\kirocrew-backend\bin\kirocrew.cmd"' in BODY
    assert r'Join-Path $resources "backend-dist\kiro-cli"' in BODY
    gateway_env = (REPO_ROOT / "website" / "electron" / "gateway-env.js").read_text("utf-8")
    assert 'const BUNDLED_KIRO_CLI_SUBDIR = ["backend-dist", "kiro-cli"];' in gateway_env
    assert 'const BUNDLED_KIRO_CLI_ENTRY = { win32: "kiro-cli.exe" };' in gateway_env
