# ======================================================================
#  Kiro Crew in one command on Windows: install it, start it, open the chat.
#
#    irm https://download.crew.kiro.dev/start.ps1 | iex
#    & ([scriptblock]::Create((irm https://download.crew.kiro.dev/start.ps1))) -Channel insider
#    & ([scriptblock]::Create((irm https://download.crew.kiro.dev/start.ps1))) -NoBrowser
#
#  The Windows counterpart of start.sh. It installs the Kiro Crew desktop app
#  with its signed installer, then runs the bundled `kirocrew start`, which
#  checks the agent harness (kiro-cli by default), starts the gateway and opens
#  the first-run chat in the browser -- or, with -NoBrowser, prints the sign-in
#  URL with a QR code.
#
#  This is not a second installer, and it holds no trust of its own. cli.sh
#  is POSIX-only, so the Windows install is the desktop app's NSIS installer,
#  downloaded from the same CDN and run unchanged. Its trust root is
#  Authenticode: the installer runs only when Windows reports its signature
#  Valid AND the signer is the publisher the desktop app's own updater demands
#  of every update (`win.signtoolOptions.publisherName` in
#  website/electron/package.json; test/test_start_ps1.py pins the two
#  together). Nothing here pins a key or a digest that could drift.
#
#  Not install.ps1: that is the cloud-mode client setup, run from a clone.
#
#  The whole body is functions called on the LAST line, so a download cut
#  short anywhere runs nothing instead of half a script. Nothing here calls
#  `exit` unless it was run as a file: under `irm | iex` that would close the
#  user's own PowerShell window.
#
#  Options / env:
#    -NoBrowser                          print the sign-in URL instead of
#                                        opening a browser
#    -Foreground                         run the gateway in this terminal
#                                        (Ctrl-C stops it) instead of in the
#                                        background
#    -SkipInstall                        use the kirocrew already installed (on
#                                        PATH, or the desktop app's bundled
#                                        CLI); installs only when there is none
#    -Channel <nightly|insider|stable>   which installer (env KIROCREW_CHANNEL;
#                                        default stable)
#    -Version <X.Y.Z>                    that exact release of the channel
#    -Cdn <base-url>                     where the installer is fetched from
#                                        (env KIROCREW_CDN_BASE)
#    KIROCREW_HOME                       the data home (default ~\.kiro\crew)
# ======================================================================
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Channel = "",
    [string]$Version = "",
    [string]$Cdn = "",
    [switch]$NoBrowser,
    [switch]$Foreground,
    [switch]$SkipInstall,
    [switch]$Help
)

function Write-KsUsage {
    Write-Host @'
Kiro Crew in one command on Windows: install it, start it, open the chat.

  irm https://download.crew.kiro.dev/start.ps1 | iex
  & ([scriptblock]::Create((irm https://download.crew.kiro.dev/start.ps1))) -Channel insider

Installs the Kiro Crew desktop app with its signed installer (downloaded from
the same CDN and run only when Windows reports a valid signature from the
publisher the app's own updater requires), then runs `kirocrew start`: it
checks the agent harness, starts the gateway and opens the first-run chat in
your browser.

Options:
  -NoBrowser                          print the sign-in URL instead of opening
                                      a browser
  -Foreground                         run the gateway in this terminal
                                      (Ctrl-C stops it)
  -SkipInstall                        use the kirocrew already installed;
                                      installs only when there is none
  -Channel <nightly|insider|stable>   which installer (env KIROCREW_CHANNEL)
  -Version <X.Y.Z>                    that exact release of the channel
  -Cdn <base-url>                     where the installer is fetched from
                                      (env KIROCREW_CDN_BASE)
'@
}

function Write-KsError([string]$Message) {
    [Console]::Error.WriteLine("kirocrew-start: $Message")
}

# The marker write start.sh and cli.sh use, for the same reason: the data home
# is agent-writable, so writing through the name could follow a planted link out
# of it. A link at the destination is removed as a link (never its target), a
# directory there is refused, and the value lands through a fresh file renamed
# into place. ASCII with a bare LF, byte for byte what start.sh writes: Windows
# PowerShell's utf8 encoding would prepend a BOM the reader does not strip.
# Bookkeeping only, so a failure warns and the start continues.
function Write-KsMarker {
    param([string]$DataHome, [string]$Name, [string]$Value)

    try {
        $null = New-Item -ItemType Directory -Force -Path $DataHome
        $dest = Join-Path $DataHome $Name
        $existing = Get-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue
        if ($existing -and ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            if ($existing.PSIsContainer) {
                [IO.Directory]::Delete($dest, $false)
            } else {
                [IO.File]::Delete($dest)
            }
        } elseif ($existing -and $existing.PSIsContainer) {
            Write-KsError "a directory occupies $dest; not recording $Name"
            return
        }
        $tmp = Join-Path $DataHome (".marker." + [guid]::NewGuid().ToString("N"))
        $stream = [IO.File]::Open($tmp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        try {
            $bytes = [Text.Encoding]::ASCII.GetBytes("$Value`n")
            $stream.Write($bytes, 0, $bytes.Length)
        } finally {
            $stream.Dispose()
        }
        Move-Item -LiteralPath $tmp -Destination $dest -Force
    } catch {
        Write-KsError "could not record $Name in ${DataHome}: $($_.Exception.Message)"
    }
}

# Every per-user desktop install this account has, identified by what it
# contains rather than by a product name (nightly installs under a different
# name so it can sit beside stable). electron-builder writes two keys per
# install: the Uninstall entry, which carries DisplayVersion, and beside it
# `HKCU\Software\<same leaf>`, which carries the InstallLocation the updater
# resolves against -- the pair scripts/smoke-windows-install.ps1 reads.
function Get-KsDesktopInstalls {
    $uninstallRoot = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
    $found = @()
    foreach ($entry in @(Get-ChildItem -LiteralPath $uninstallRoot -ErrorAction SilentlyContinue)) {
        $info = Get-ItemProperty -LiteralPath "HKCU:\Software\$($entry.PSChildName)" -ErrorAction SilentlyContinue
        if (-not $info) { continue }
        $location = $info.PSObject.Properties["InstallLocation"]
        if (-not $location -or -not $location.Value) { continue }
        $cli = Join-Path ([string]$location.Value) "resources\backend-dist\kirocrew-backend\bin\kirocrew.cmd"
        if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) { continue }
        $registration = Get-ItemProperty -LiteralPath $entry.PSPath -ErrorAction SilentlyContinue
        $displayVersion = ""
        if ($registration -and $registration.PSObject.Properties["DisplayVersion"]) {
            $displayVersion = [string]$registration.DisplayVersion
        }
        $found += [pscustomobject]@{ Key = $entry.PSChildName; Version = $displayVersion; Cli = $cli }
    }
    return $found
}

# Where `kirocrew` already is: PATH first (what typing the command would run),
# then the bundled CLI of the one desktop install there is. The desktop
# installer makes no PATH edit, so a desktop-only machine has no `kirocrew` on
# PATH at all.
function Find-KsKirocrew {
    $onPath = Get-Command kirocrew -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($onPath) { return $onPath.Path }
    $installs = @(Get-KsDesktopInstalls)
    if ($installs.Count -eq 1) { return $installs[0].Cli }
    return $null
}

# The kiro-cli the desktop app bundles beside the CLI it runs, handed to
# `kirocrew start` the way the app's own launcher hands it to the gateway
# (website/electron/gateway-env.js): KIROCREW_BUNDLED_KIRO_DIR, plus
# KIRO_NO_AUTO_UPDATE so the signed copy never rewrites itself. Only for the
# desktop install's own bundled CLI, and only when the staged exe answers
# `--version` here; otherwise nothing is set and the user's own kiro-cli is
# looked up exactly as before. An operator's KIROCREW_KIRO_BIN still wins.
function Get-KsBundledKiroEnv {
    param([string]$Kirocrew)

    $suffix = "\resources\backend-dist\kirocrew-backend\bin\kirocrew.cmd"
    $cli = $Kirocrew.Replace("/", "\")
    if (-not $cli.EndsWith($suffix, [StringComparison]::OrdinalIgnoreCase)) { return @{} }
    $resources = $cli.Substring(0, $cli.Length - $suffix.Length) + "\resources"
    $dir = Join-Path $resources "backend-dist\kiro-cli"
    $exe = Join-Path $dir "kiro-cli.exe"
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return @{} }
    $saved = $env:KIRO_NO_AUTO_UPDATE
    $env:KIRO_NO_AUTO_UPDATE = "1"
    try {
        # Not Start-Process: that is kept for the one downloaded file this
        # script runs, the signature-checked installer. This runs a file the
        # installed app already holds, with no window, bounded like the app's
        # own probe.
        $info = New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName = $exe
        $info.Arguments = "--version"
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $probe = [System.Diagnostics.Process]::Start($info)
        if (-not $probe.WaitForExit(10000)) {
            try { $probe.Kill() } catch { }
            return @{}
        }
        if ($probe.ExitCode -ne 0) { return @{} }
    } catch {
        return @{}
    } finally {
        $env:KIRO_NO_AUTO_UPDATE = $saved
    }
    return @{ KIROCREW_BUNDLED_KIRO_DIR = $dir; KIRO_NO_AUTO_UPDATE = "1" }
}

function Invoke-KsInstall {
    param([string]$Url, [string]$Publisher)

    # Windows PowerShell 5.1 on an older build may still offer TLS 1.0 first.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("kirocrew-setup-" + [guid]::NewGuid().ToString("N") + ".exe")
    try {
        Write-Host "Downloading the Kiro Crew installer from $Url ..."
        try {
            Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing
        } catch {
            Write-KsError "could not download ${Url}: $($_.Exception.Message)"
            return 1
        }

        # The same verdict electron-updater requires before it runs an update:
        # Windows says the signature is Valid (chain, integrity, revocation), and
        # the signing certificate's CN is the pinned publisher.
        $signature = Get-AuthenticodeSignature -LiteralPath $tmp
        if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
            Write-KsError "the downloaded installer's signature is $($signature.Status), not Valid; refusing to run it ($($signature.StatusMessage))"
            return 1
        }
        $signer = $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
        if ($signer -cne $Publisher) {
            Write-KsError "the downloaded installer is signed by '$signer', not '$Publisher'; refusing to run it"
            return 1
        }
        Write-Host "Verified the installer's signature ($Publisher)."

        Write-Host "Installing Kiro Crew ..."
        # /S silent, /currentuser so no elevation is asked for; the same two
        # flags the Windows install smoke test runs it with.
        $process = Start-Process -FilePath $tmp -ArgumentList @("/S", "/currentuser") -PassThru
        # Caches the process handle, without which Windows PowerShell 5.1 can
        # report a null ExitCode for a process started with -PassThru.
        $null = $process.Handle
        if (-not $process.WaitForExit(600000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            Write-KsError "the installer did not finish within 10 minutes"
            return 1
        }
        if ($process.ExitCode -ne 0) {
            Write-KsError "the installer failed with exit status $($process.ExitCode)"
            return [int]$process.ExitCode
        }
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
    return 0
}

# The bundled CLI of the install that just ran, told apart from any other
# desktop install by what changed since $Before.
function Wait-KsInstalledCli {
    param([object[]]$Before)

    # The registration can land after the installer's own process exits, so it
    # is polled with a ceiling, never slept toward. An install that changed
    # nothing (the same version again) is found by being the only one.
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $changed = @()
    $after = @()
    $waiting = $false
    do {
        $after = @(Get-KsDesktopInstalls)
        $changed = @($after | Where-Object {
                $now = $_
                -not @($Before | Where-Object { $_.Key -eq $now.Key -and $_.Version -eq $now.Version -and $_.Cli -eq $now.Cli })
            })
        if ($changed.Count -eq 0) {
            if (-not $waiting) { Write-Host "Waiting for the install to register ..." }
            $waiting = $true
            Start-Sleep -Milliseconds 500
        }
    } while ($changed.Count -eq 0 -and [DateTime]::UtcNow -lt $deadline)
    if ($changed.Count -eq 1) { return $changed[0].Cli }
    if ($changed.Count -eq 0 -and $after.Count -eq 1) { return $after[0].Cli }
    $candidates = @($(if ($changed.Count -gt 0) { $changed } else { $after }) | ForEach-Object { $_.Cli })
    if ($candidates.Count -eq 0) {
        Write-KsError "Kiro Crew was installed but its bundled CLI cannot be found; open Kiro Crew from the Start menu instead"
    } else {
        Write-KsError "Kiro Crew is installed more than once here; start the one you want with:"
        foreach ($candidate in $candidates) { Write-Host "  & '$candidate' start" }
    }
    return 1
}

function Test-KsWindows {
    return [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
}

function Resolve-KsKirocrew {
    param([string]$Channel, [string]$Version, [string]$Cdn, [bool]$SkipInstall)

    # The Windows installer's publisher, exactly as the desktop app's updater
    # pins it.
    $publisher = "Amazon Web Services, Inc."
    $channels = @("nightly", "insider", "stable")

    if (-not $Channel) { $Channel = $env:KIROCREW_CHANNEL }
    if (-not $Channel) { $Channel = "stable" }
    $Channel = $Channel.ToLowerInvariant()
    if ($channels -notcontains $Channel) {
        Write-KsError "unknown channel '$Channel' (want: $($channels -join ', '))"
        return 2
    }
    # One path segment of the installer URL, so nothing but a version shape.
    if ($Version -and $Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$') {
        Write-KsError "-Version wants X.Y.Z, not '$Version'"
        return 2
    }
    $base = $Cdn
    if (-not $base) { $base = $env:KIROCREW_CDN_BASE }
    if (-not $base) { $base = "https://download.crew.kiro.dev" }
    $base = $base.TrimEnd("/")
    if (-not $base.StartsWith("https://", [StringComparison]::OrdinalIgnoreCase)) {
        Write-KsError "-Cdn must be an https:// URL, not '$base'"
        return 2
    }
    $dataHome = $env:KIROCREW_HOME
    if (-not $dataHome) { $dataHome = Join-Path $HOME ".kiro\crew" }

    if ($SkipInstall) {
        $found = Find-KsKirocrew
        if ($found) {
            Write-Host "Using the installed kirocrew at $found (-SkipInstall)."
            return $found
        }
    }
    if (-not (Test-KsWindows)) {
        Write-KsError "start.ps1 installs the Windows app; on macOS and Linux run: curl -fsSL https://download.crew.kiro.dev/start.sh | sh"
        return 1
    }
    $release = "latest"
    if ($Version) { $release = $Version }
    $before = @(Get-KsDesktopInstalls)
    $installed = Invoke-KsInstall -Url "$base/desktop/$Channel/$release/KiroCrew-Setup.exe" -Publisher $publisher
    if ($installed -ne 0) { return $installed }
    Write-KsMarker -DataHome $dataHome -Name 'install-origin' -Value 'start'
    return Wait-KsInstalledCli -Before $before
}

function Invoke-KsStart {
    param(
        [string]$Channel,
        [string]$Version,
        [string]$Cdn,
        [bool]$NoBrowser,
        [bool]$Foreground,
        [bool]$SkipInstall,
        [bool]$Help,
        [bool]$FromFile
    )

    # Function scope, so none of these leak into the caller's session under
    # `irm | iex`; the functions called from here inherit them. The progress
    # bar is off because it slows Invoke-WebRequest by an order of magnitude
    # on Windows PowerShell 5.1.
    Set-StrictMode -Version 3.0
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"

    $code = 0
    if ($Help) {
        Write-KsUsage
    } else {
        $kirocrew = Resolve-KsKirocrew -Channel $Channel -Version $Version -Cdn $Cdn -SkipInstall $SkipInstall
        if ($kirocrew -is [int]) {
            $code = $kirocrew
        } else {
            $startArgs = @("start")
            if ($NoBrowser) { $startArgs += "--no-browser" }
            if ($Foreground) { $startArgs += "--foreground" }
            Write-Host ""
            # Set for the child only, and put back after: under `irm | iex` this
            # is the user's own session.
            $bundled = Get-KsBundledKiroEnv -Kirocrew ([string]$kirocrew)
            $saved = @{}
            foreach ($name in $bundled.Keys) {
                $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
                [Environment]::SetEnvironmentVariable($name, $bundled[$name], "Process")
            }
            try {
                # Not captured and not piped, so `kirocrew start` keeps this
                # console: kiro-cli's own sign-in may run there, and its output streams live.
                & $kirocrew @startArgs
                $code = $LASTEXITCODE
            } finally {
                foreach ($name in $saved.Keys) {
                    [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process")
                }
            }
        }
    }
    # Run as a file, the exit status is the script's. Under `irm | iex` it is
    # left in $LASTEXITCODE instead, since `exit` would end the user's session.
    if ($FromFile) { exit $code }
    $global:LASTEXITCODE = $code
}

Invoke-KsStart -Channel $Channel -Version $Version -Cdn $Cdn -NoBrowser $NoBrowser.IsPresent -Foreground $Foreground.IsPresent -SkipInstall $SkipInstall.IsPresent -Help $Help.IsPresent -FromFile ([bool]$PSCommandPath)
