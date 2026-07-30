#Requires -Version 5.1
<#
  OpenAI4S launcher for Windows.

  This package does NOT run OpenAI4S on native Windows, and that is deliberate
  rather than a limitation of the packaging. The kernel spawns POSIX
  subprocesses, the R channel rides file descriptors 3 and 4 through a shell
  redirection, and the OS sandbox has no Windows backend — so
  openai4s/platform_support.py refuses to start a kernel on win32 instead of
  warning and proceeding. A Windows build that started anyway would leave a
  scientist to discover the problem from a half-working analysis, which is
  precisely the failure this product cannot afford.

  So the Windows deliverable is the documented supported route, made
  double-clickable: the package carries the Linux bundle, installs it into your
  WSL2 distribution on first launch, starts the daemon there, and opens the
  Windows browser at the forwarded localhost port. WSL2 reports as Linux, which
  is a supported platform, so nothing is being worked around — the app runs on
  the platform it says it runs on.

  Usage:
    OpenAI4S.cmd                 start the daemon and open the web UI
    OpenAI4S.cmd status          ask the daemon whether it is up
    OpenAI4S.cmd setup           any openai4s CLI command, run inside WSL2

  Environment:
    OPENAI4S_WSL_DISTRO   use this distribution instead of the default
    OPENAI4S_HOST         default 127.0.0.1
    OPENAI4S_PORT         default 8760
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

$ErrorActionPreference = 'Stop'

# wsl.exe emits UTF-16LE by default, which turns every parsed line into
# null-separated mush. WSL 0.64+ honours this; older builds are handled by the
# null-stripping in Get-WslDistros.
$env:WSL_UTF8 = '1'

$Here = $PSScriptRoot
$AppHost = if ($env:OPENAI4S_HOST) { $env:OPENAI4S_HOST } else { '127.0.0.1' }
$AppPort = if ($env:OPENAI4S_PORT) { $env:OPENAI4S_PORT } else { '8760' }
$Url = "http://${AppHost}:${AppPort}/"

function Write-Section([string] $Text) {
    Write-Host ''
    Write-Host $Text -ForegroundColor Cyan
}

function Stop-WithGuidance([string] $Problem, [string[]] $Steps) {
    Write-Host ''
    Write-Host "OpenAI4S cannot start: $Problem" -ForegroundColor Red
    if ($Steps) {
        Write-Host ''
        foreach ($step in $Steps) { Write-Host "  $step" }
    }
    # Double-clicked from Explorer, the console window closes the instant this
    # returns and the user sees the guidance for about a frame. The pause is
    # skipped when something is driving the launcher — CI, or a script — where
    # there is nobody to press a key and blocking would look like a hang.
    if (-not $env:OPENAI4S_NONINTERACTIVE) {
        Write-Host ''
        Write-Host 'Press Enter to close.' -ForegroundColor DarkGray
        try { [void](Read-Host) } catch { }
    }
    exit 1
}

function Get-PackageFacts {
    $versionFile = Join-Path $Here 'VERSION'
    if (-not (Test-Path $versionFile)) {
        Stop-WithGuidance 'this package is incomplete (no VERSION file).' @(
            'Re-download the release archive and unzip it again.'
        )
    }
    $version = (Get-Content $versionFile -Raw).Trim()

    $payload = Get-ChildItem -Path (Join-Path $Here 'payload') -Filter '*.tar.gz' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $payload) {
        Stop-WithGuidance 'this package carries no Linux payload.' @(
            'Re-download the release archive and unzip the whole thing —',
            'the payload folder is not optional.'
        )
    }
    $digestFile = "$($payload.FullName).sha256"
    if (-not (Test-Path $digestFile)) {
        Stop-WithGuidance 'the payload has no checksum file beside it.' @(
            'Re-download the release archive and unzip it again.'
        )
    }
    # `<digest>  <name>` — the same one-line format both build scripts publish.
    $digest = ((Get-Content $digestFile -Raw).Trim() -split '\s+')[0]

    [pscustomobject]@{
        Version     = $version
        PayloadPath = $payload.FullName
        PayloadName = $payload.Name
        Digest      = $digest
        # The directory the tarball unpacks into, which is its own name minus
        # the two archive suffixes.
        BundleDir   = $payload.Name -replace '\.tar\.gz$', ''
    }
}

function Get-WslDistros {
    if (-not (Get-Command 'wsl.exe' -ErrorAction SilentlyContinue)) {
        Stop-WithGuidance 'WSL is not installed on this machine.' @(
            'OpenAI4S runs on Linux; on Windows it runs inside WSL2.',
            '',
            'Open PowerShell as Administrator and run:',
            '    wsl --install',
            '',
            'Reboot when it asks, finish the Ubuntu username/password prompt,',
            'then run OpenAI4S.cmd again.'
        )
    }

    $raw = & wsl.exe --list --verbose 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-WithGuidance 'WSL is present but has no installed distribution.' @(
            'Open PowerShell as Administrator and run:',
            '    wsl --install -d Ubuntu',
            '',
            'Finish the username/password prompt, then run OpenAI4S.cmd again.'
        )
    }

    $distros = @()
    foreach ($line in @($raw)) {
        # Strip the UTF-16 nulls an older wsl.exe emits despite WSL_UTF8.
        $text = ([string]$line) -replace "`0", ''
        $text = $text.Trim()
        if (-not $text) { continue }
        $isDefault = $text.StartsWith('*')
        $text = $text.TrimStart('*').Trim()
        $fields = $text -split '\s+'
        if ($fields.Count -lt 3) { continue }
        # The header row, in whatever language Windows is running in, is the one
        # whose last field is not a number.
        $wslVersion = 0
        if (-not [int]::TryParse($fields[-1], [ref] $wslVersion)) { continue }
        $distros += [pscustomobject]@{
            Name      = $fields[0]
            State     = $fields[-2]
            Version   = $wslVersion
            IsDefault = $isDefault
        }
    }
    return $distros
}

function Select-Distro {
    $distros = Get-WslDistros
    if (-not $distros -or $distros.Count -eq 0) {
        Stop-WithGuidance 'no WSL distribution is installed.' @(
            'Open PowerShell as Administrator and run:',
            '    wsl --install -d Ubuntu'
        )
    }

    if ($env:OPENAI4S_WSL_DISTRO) {
        $named = $distros | Where-Object { $_.Name -eq $env:OPENAI4S_WSL_DISTRO } | Select-Object -First 1
        if (-not $named) {
            Stop-WithGuidance "OPENAI4S_WSL_DISTRO names '$($env:OPENAI4S_WSL_DISTRO)', which is not installed." @(
                'Installed distributions:',
                ($distros | ForEach-Object { "    $($_.Name)  (WSL $($_.Version))" })
            )
        }
        if ($named.Version -ne 2) {
            Stop-WithGuidance "'$($named.Name)' is a WSL 1 distribution." @(
                'WSL 1 emulates Linux syscalls and has no user namespaces, so the',
                'kernel sandbox cannot start and cells would run unisolated.',
                '',
                'Convert it:',
                "    wsl --set-version $($named.Name) 2"
            )
        }
        return $named.Name
    }

    $two = @($distros | Where-Object { $_.Version -eq 2 })
    if ($two.Count -eq 0) {
        $names = ($distros | ForEach-Object { $_.Name }) -join ', '
        Stop-WithGuidance "every installed distribution ($names) is WSL 1." @(
            'WSL 1 emulates Linux syscalls and has no user namespaces, so the',
            'kernel sandbox cannot start and cells would run unisolated.',
            '',
            'Convert one:',
            "    wsl --set-version $($distros[0].Name) 2",
            '',
            'Or set OPENAI4S_WSL_DISTRO to a WSL 2 distribution.'
        )
    }
    $preferred = $two | Where-Object { $_.IsDefault } | Select-Object -First 1
    if ($preferred) { return $preferred.Name }
    return $two[0].Name
}

function ConvertTo-WslPath([string] $Distro, [string] $WindowsPath) {
    $translated = & wsl.exe -d $Distro --exec wslpath -a "$WindowsPath" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-WithGuidance "WSL could not reach this folder: $WindowsPath" @(
            'Unzip the package onto a local drive (for example C:\OpenAI4S).',
            'A network share or a OneDrive placeholder folder is not always',
            'visible from inside WSL.'
        )
    }
    return (([string](@($translated)[0])) -replace "`0", '').Trim()
}

function Invoke-Bootstrap([string] $Distro, [string] $BootstrapLinux, [string[]] $BootstrapArgs) {
    # --exec: run without an intervening login shell, so argv reaches the script
    # as-is. Plain `wsl <command>` hands the line to the default shell, which
    # re-splits it and breaks on the spaces an unzipped Downloads path is full
    # of. `sh <script>` rather than `./<script>` because the package sits on a
    # DrvFs mount, where the executable bit is not reliably honoured.
    & wsl.exe -d $Distro --exec sh $BootstrapLinux @BootstrapArgs
    return $LASTEXITCODE
}

function Test-Serving {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($AppHost, [int] $AppPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(400, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# ---------------------------------------------------------------------------

# WSL first, package second. Both are fatal, but "you have no WSL2" is the one
# a user has to fix before anything else in this package means anything — and
# checking it first is also what lets the refusal be exercised against a bare
# checkout, on a runner that has no WSL, without staging a package.
$distro = Select-Distro
$facts = Get-PackageFacts
$packageLinux = ConvertTo-WslPath $distro $Here
$bootstrap = "$packageLinux/wsl/bootstrap.sh"
$payloadLinux = "$packageLinux/payload/$($facts.PayloadName)"

# A CLI passthrough must not start anything or open a browser: `OpenAI4S.cmd
# status` is a question, and answering it by launching the daemon would make the
# answer always yes.
if ($Arguments -and $Arguments.Count -gt 0) {
    $code = Invoke-Bootstrap $distro $bootstrap (@('install', $payloadLinux, $facts.Digest, $facts.BundleDir))
    if ($code -ne 0) { exit $code }
    exit (Invoke-Bootstrap $distro $bootstrap (@('cli', $facts.BundleDir) + $Arguments))
}

if (Test-Serving) {
    Write-Host "OpenAI4S is already serving at $Url — opening it." -ForegroundColor Green
    Start-Process $Url
    exit 0
}

Write-Section "OpenAI4S $($facts.Version) — starting in WSL2 ($distro)"

Write-Host '  [1/3] installing the Linux bundle (first run only, ~1 GB unpacked)...'
$code = Invoke-Bootstrap $distro $bootstrap (@('install', $payloadLinux, $facts.Digest, $facts.BundleDir))
if ($code -ne 0) {
    Stop-WithGuidance 'the Linux bundle could not be installed into WSL.' @(
        'The message above says why. The usual causes are a full disk inside',
        'the distribution, or the package sitting on a folder WSL cannot read.'
    )
}

Write-Host '  [2/3] starting the daemon...'
$code = Invoke-Bootstrap $distro $bootstrap (@('serve', $facts.BundleDir, $AppHost, $AppPort))
if ($code -ne 0) {
    Stop-WithGuidance 'the daemon did not start.' @(
        'Read the log from inside WSL:',
        "    wsl -d $distro -- tail -40 ~/.openai4s/logs/app.out"
    )
}

Write-Host '  [3/3] waiting for the web UI...'
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    if (Test-Serving) {
        Write-Host ''
        Write-Host "  ready: $Url" -ForegroundColor Green
        Start-Process $Url
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

Stop-WithGuidance "the daemon started but $Url never answered within 60s." @(
    'Read the log from inside WSL:',
    "    wsl -d $distro -- tail -40 ~/.openai4s/logs/app.out",
    '',
    'If the log looks healthy, WSL localhost forwarding may be off. Check for',
    'a [wsl2] localhostForwarding=false line in %USERPROFILE%\.wslconfig, then',
    '    wsl --shutdown',
    'and run OpenAI4S.cmd again.'
)
