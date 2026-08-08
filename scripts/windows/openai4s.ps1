#Requires -Version 5.1
<#
  OpenAI4S launcher for Windows.

  ASCII ONLY, and not as a style preference. `OpenAI4S.cmd` invokes
  `powershell.exe` -- Windows PowerShell 5.1, which is what a user actually
  double-clicks -- and 5.1 reads a .ps1 without a BOM as ANSI, not UTF-8. A
  UTF-8 em dash decodes under cp1252 to three characters ending in 0x94, which
  is U+201D, which PowerShell accepts as a closing double quote: a dash inside
  a string literal silently ends the string and the parse collapses far below
  it. This file had exactly that, and pwsh 7 parsed it happily while 5.1 failed
  at `if (Test-Serving) {`, 200 lines away from the real cause.
  `verify_windows_zip.py` fails the build on any non-ASCII byte here.

  This package does NOT run OpenAI4S on native Windows, and that is deliberate
  rather than a limitation of the packaging. The kernel spawns POSIX
  subprocesses, the R channel rides file descriptors 3 and 4 through a shell
  redirection, and the OS sandbox has no Windows backend -- so
  openai4s/platform_support.py refuses to start a kernel on win32 instead of
  warning and proceeding. A Windows build that started anyway would leave a
  scientist to discover the problem from a half-working analysis, which is
  precisely the failure this product cannot afford.

  So the Windows deliverable is the documented supported route, made
  double-clickable: the package carries the Linux bundle, installs it into your
  WSL2 distribution on first launch, starts the daemon there, and opens the
  Windows browser at the forwarded localhost port. WSL2 reports as Linux, which
  is a supported platform, so nothing is being worked around -- the app runs on
  the platform it says it runs on.

  Usage:
    OpenAI4S.cmd                 start the daemon and open the web UI
    OpenAI4S.cmd status          ask the daemon whether it is up
    OpenAI4S.cmd setup           any openai4s CLI command, run inside WSL2

  Environment:
    OPENAI4S_WSL_DISTRO   use this distribution instead of the default
    OPENAI4S_WSL_PROXY    proxy reachable from WSL (for example
                          http://127.0.0.1:7897 with mirrored networking)
    OPENAI4S_WSL_PYPI_INDEX  Python mirror used by later package installs
    OPENAI4S_WSL_CONDA_MIRROR  Conda mirror root used by environment setup
    OPENAI4S_WSL_DATA_DIR  optional absolute Linux data path (default ~/.openai4s)
    OPENAI4S_HOST         default 127.0.0.1
    OPENAI4S_PORT         default 8760
    OPENAI4S_NO_OPEN      set to 1 to print readiness without opening a browser
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
$WslProxy = $env:OPENAI4S_WSL_PROXY
$WslDataDir = $env:OPENAI4S_WSL_DATA_DIR
$WslLogPath = if ($WslDataDir) {
    "$WslDataDir/logs/app.out"
} else {
    '~/.openai4s/logs/app.out'
}
$PypiIndex = if ($env:OPENAI4S_WSL_PYPI_INDEX) {
    $env:OPENAI4S_WSL_PYPI_INDEX
} else {
    'https://pypi.tuna.tsinghua.edu.cn/simple'
}
$CondaMirror = if ($env:OPENAI4S_WSL_CONDA_MIRROR) {
    $env:OPENAI4S_WSL_CONDA_MIRROR
} else {
    'https://mirrors.tuna.tsinghua.edu.cn/anaconda'
}

function Write-Section([string] $Text) {
    Write-Host ''
    Write-Host $Text -ForegroundColor Cyan
}

function Open-AppUrl([string] $appUrl) {
    if ($env:OPENAI4S_NO_OPEN) {
        Write-Host '  browser open suppressed by OPENAI4S_NO_OPEN.' -ForegroundColor DarkGray
        return
    }
    Start-Process $appUrl
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
    # skipped when something is driving the launcher -- CI, or a script -- where
    # there is nobody to press a key and blocking would look like a hang.
    if (-not $env:OPENAI4S_NONINTERACTIVE) {
        Write-Host ''
        Write-Host 'Press Enter to close.' -ForegroundColor DarkGray
        try { [void](Read-Host) } catch { }
    }
    exit 1
}

function Invoke-WslCaptureNative([string[]] $WslArgs) {
    # Windows PowerShell 5.1 converts native stderr into ErrorRecord objects.
    # With this script's fail-fast ErrorActionPreference, a harmless WSL
    # diagnostic (notably the NAT localhost-proxy warning) otherwise aborts a
    # command whose native exit code is zero. Native tools are authoritative
    # through LASTEXITCODE; capture both streams while that one call is allowed
    # to continue, then restore the script-wide fail-fast policy.
    $previousPreference = $ErrorActionPreference
    $output = @()
    $code = 1
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& wsl.exe @WslArgs 2>&1)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return [pscustomobject]@{
        ExitCode = $code
        Output   = $output
    }
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
            'Re-download the release archive and unzip the whole thing --',
            'the payload folder is not optional.'
        )
    }
    $digestFile = "$($payload.FullName).sha256"
    if (-not (Test-Path $digestFile)) {
        Stop-WithGuidance 'the payload has no checksum file beside it.' @(
            'Re-download the release archive and unzip it again.'
        )
    }
    # `<digest>  <name>` -- the same one-line format both build scripts publish.
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
            '    wsl --install -d Ubuntu-24.04',
            '',
            'Reboot when it asks, finish the Ubuntu username/password prompt,',
            'then run OpenAI4S.cmd again.'
        )
    }

    $listing = Invoke-WslCaptureNative -WslArgs @('--list', '--verbose')
    $raw = $listing.Output
    if ($listing.ExitCode -ne 0) {
        Stop-WithGuidance 'WSL is present but has no installed distribution.' @(
            'Open PowerShell as Administrator and run:',
            '    wsl --install -d Ubuntu-24.04',
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
            '    wsl --install -d Ubuntu-24.04'
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
    # Ubuntu 24.04 carries bubblewrap >= 0.8.0. Prefer it when an older distro
    # is still the Windows default; the explicit environment override above
    # remains authoritative for people using another compatible distribution.
    $preferred = $two | Where-Object { $_.Name -eq 'Ubuntu-24.04' } | Select-Object -First 1
    if ($preferred) { return $preferred.Name }
    $preferred = $two | Where-Object { $_.IsDefault } | Select-Object -First 1
    if ($preferred) { return $preferred.Name }
    return $two[0].Name
}

function Test-LocalhostForwardingDisabled {
    # `localhostForwarding=false` is an explicit request, not a transient
    # connection failure. In NAT mode it means a daemon bound to WSL loopback
    # can never be reached by the Windows browser. Mirrored networking has one
    # shared loopback and therefore does not need the NAT-address fallback.
    $configPath = Join-Path $env:USERPROFILE '.wslconfig'
    if (-not (Test-Path -LiteralPath $configPath)) { return $false }

    $section = ''
    $disabled = $false
    $mirrored = $false
    foreach ($rawLine in Get-Content -LiteralPath $configPath) {
        $line = ([string] $rawLine).Trim()
        if ($line -match '^\[([^]]+)\]$') {
            $section = $Matches[1].Trim().ToLowerInvariant()
            continue
        }
        if ($section -ne 'wsl2' -or -not $line -or $line.StartsWith('#')) {
            continue
        }
        if ($line -match '^localhostForwarding\s*=\s*([^#;]+)') {
            $value = $Matches[1].Trim().ToLowerInvariant()
            $disabled = $value -in @('false', '0', 'no', 'off')
        }
        if ($line -match '^networkingMode\s*=\s*([^#;]+)') {
            $mirrored = $Matches[1].Trim().ToLowerInvariant() -eq 'mirrored'
        }
    }
    return $disabled -and -not $mirrored
}

function Get-WslIpv4([string] $Distro) {
    # eth0 is the WSL NAT interface Windows can route back to. Prefer it over
    # docker0, VPNs, and other bridges regardless of hostname -I ordering.
    $primary = Invoke-WslCaptureNative -WslArgs @(
        '-d', $Distro, '--exec', 'ip', '-4', '-o', 'addr', 'show',
        'dev', 'eth0', 'scope', 'global'
    )
    if ($primary.ExitCode -eq 0) {
        foreach ($item in @($primary.Output)) {
            $clean = (([string] $item) -replace "`0", '').Trim()
            $tokens = @($clean -split '\s+')
            for ($index = 0; $index -lt ($tokens.Count - 1); $index++) {
                if ($tokens[$index] -ne 'inet') { continue }
                $candidate = @($tokens[$index + 1] -split '/', 2)[0]
                $address = $null
                if ([Net.IPAddress]::TryParse($candidate, [ref] $address) -and
                    $address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
                    -not [Net.IPAddress]::IsLoopback($address)) {
                    return $address.IPAddressToString
                }
            }
        }
    }

    # Non-standard WSL distributions may rename the primary interface. Ask
    # Linux routing for their default-path source before the broad fallback.
    $route = Invoke-WslCaptureNative -WslArgs @(
        '-d', $Distro, '--exec', 'ip', '-4', 'route', 'get', '192.0.2.1'
    )
    if ($route.ExitCode -eq 0) {
        foreach ($item in @($route.Output)) {
            $clean = (([string] $item) -replace "`0", '').Trim()
            $tokens = @($clean -split '\s+')
            for ($index = 0; $index -lt ($tokens.Count - 1); $index++) {
                if ($tokens[$index] -ne 'src') { continue }
                $address = $null
                if ([Net.IPAddress]::TryParse($tokens[$index + 1], [ref] $address) -and
                    $address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
                    -not [Net.IPAddress]::IsLoopback($address)) {
                    return $address.IPAddressToString
                }
            }
        }
    }

    # Old/minimal distributions may not carry iproute2. Keep a compatibility
    # fallback, but only after the route-selected address was unavailable.
    $result = Invoke-WslCaptureNative -WslArgs @(
        '-d', $Distro, '--exec', 'hostname', '-I'
    )
    if ($result.ExitCode -ne 0) { return $null }
    foreach ($item in @($result.Output)) {
        $clean = (([string] $item) -replace "`0", '').Trim()
        foreach ($token in $clean -split '\s+') {
            $address = $null
            if ([Net.IPAddress]::TryParse($token, [ref] $address) -and
                $address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
                -not [Net.IPAddress]::IsLoopback($address)) {
                return $address.IPAddressToString
            }
        }
    }
    return $null
}

function ConvertTo-WslPath([string] $Distro, [string] $WindowsPath) {
    $result = Invoke-WslCaptureNative -WslArgs @(
        '-d', $Distro, '--exec', 'wslpath', '-a', $WindowsPath
    )
    $translated = $result.Output
    if ($result.ExitCode -ne 0) {
        Stop-WithGuidance "WSL could not reach this folder: $WindowsPath" @(
            'Unzip the package onto a local drive (for example C:\OpenAI4S).',
            'A network share or a OneDrive placeholder folder is not always',
            'visible from inside WSL.'
        )
    }
    # The outer @() is load-bearing: PowerShell unwraps a one-item pipeline to
    # a scalar string, and indexing that scalar returns its first character.
    $paths = @(
        @($translated) | ForEach-Object {
            (([string] $_) -replace "`0", '').Trim()
        } | Where-Object { $_.StartsWith('/') }
    )
    if (-not $paths) {
        Stop-WithGuidance "WSL returned no Linux path for: $WindowsPath" @(
            'Unzip the package onto a local drive and try again.'
        )
    }
    $selectedPath = $paths[0]
    return [string]$selectedPath
}

function Assert-HttpUrl([string] $Name, [string] $Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return }
    $parsed = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref] $parsed) -or
        $parsed.Scheme -notin @('http', 'https') -or
        -not [string]::IsNullOrEmpty($parsed.UserInfo)) {
        Stop-WithGuidance "$Name is not an absolute HTTP(S) URL." @(
            'Use a credential-free value such as http://127.0.0.1:7897.'
        )
    }
}

function Assert-WslDataDir([string] $Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return }
    $parts = $Value -split '/'
    if (-not $Value.StartsWith('/') -or $Value -eq '/' -or $parts -contains '..' -or
        $Value -match "[`r`n]") {
        Stop-WithGuidance 'OPENAI4S_WSL_DATA_DIR must be a specific absolute Linux path.' @(
            'Example: /home/me/.openai4s'
        )
    }
}

function Get-WslBootstrapArgs([string] $Distro, [string] $BootstrapLinux, [string[]] $BootstrapArgs) {
    $wslArgs = @('-d', $Distro, '--exec', 'env')
    $wslArgs += "OPENAI4S_HOST=$AppHost"
    $wslArgs += "OPENAI4S_PORT=$AppPort"
    if ($WslDataDir) {
        $wslArgs += "OPENAI4S_DATA_DIR=$WslDataDir"
    }
    $wslArgs += "OPENAI4S_PYPI_INDEX_URL=$PypiIndex"
    $wslArgs += "PIP_INDEX_URL=$PypiIndex"
    $wslArgs += "UV_DEFAULT_INDEX=$PypiIndex"
    $wslArgs += "OPENAI4S_CONDA_MIRROR=$CondaMirror"
    $proxyBypass = "127.0.0.1,localhost,$AppHost"
    $wslArgs += "NO_PROXY=$proxyBypass"
    $wslArgs += "no_proxy=$proxyBypass"
    if ($WslProxy) {
        $wslArgs += "HTTP_PROXY=$WslProxy"
        $wslArgs += "HTTPS_PROXY=$WslProxy"
        $wslArgs += "http_proxy=$WslProxy"
        $wslArgs += "https_proxy=$WslProxy"
    }
    $wslArgs += @('sh', $BootstrapLinux)
    $wslArgs += $BootstrapArgs
    return $wslArgs
}

function Invoke-Bootstrap([string] $Distro, [string] $BootstrapLinux, [string[]] $BootstrapArgs) {
    # --exec: run without an intervening login shell, so argv reaches the script
    # as-is. Plain `wsl <command>` hands the line to the default shell, which
    # re-splits it and breaks on the spaces an unzipped Downloads path is full
    # of. `sh <script>` rather than `./<script>` because the package sits on a
    # DrvFs mount, where the executable bit is not reliably honoured.
    #
    # `| Out-Host` is load-bearing, not cosmetic. A native command's stdout
    # goes to the *success stream*, which in PowerShell is the function's
    # return value -- so without the pipe, `return $LASTEXITCODE` appends to
    # bootstrap.sh's own output instead of replacing it, and the caller gets
    # @('installed /home/.../OpenAI4S-...', 0) rather than 0. bootstrap.sh
    # prints on bare stdout in every success path ("already-installed",
    # "installed", "serving http://...") and sends failures to stderr, so this
    # bites precisely when the install *worked*: `$code -ne 0` filters the
    # array to one non-zero element, `if` reads a non-empty array as true, and
    # the launcher reports "the Linux bundle could not be installed" after
    # installing it. `exit $code` then fails to convert Object[] to Int32.
    # Out-Host keeps $LASTEXITCODE intact and restores the console message the
    # failure guidance promises the reader.
    $wslArgs = Get-WslBootstrapArgs $Distro $BootstrapLinux $BootstrapArgs
    $previousPreference = $ErrorActionPreference
    $code = 1
    try {
        $ErrorActionPreference = 'Continue'
        & wsl.exe @wslArgs 2>&1 | ForEach-Object { Write-Host ([string] $_) }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return $code
}

function Invoke-BootstrapCapture([string] $Distro, [string] $BootstrapLinux, [string[]] $BootstrapArgs) {
    $wslArgs = Get-WslBootstrapArgs $Distro $BootstrapLinux $BootstrapArgs
    $result = Invoke-WslCaptureNative -WslArgs $wslArgs
    $lines = @($result.Output | ForEach-Object { (([string] $_) -replace "`0", '').Trim() })
    return [pscustomobject]@{
        ExitCode = $result.ExitCode
        Lines    = $lines
    }
}

function Get-AppUrl([string] $Distro, [string] $BootstrapLinux, [string] $BundleDir) {
    $result = Invoke-BootstrapCapture $Distro $BootstrapLinux @('cli', $BundleDir, 'url')
    if ($result.ExitCode -ne 0) {
        Stop-WithGuidance 'the secure browser URL could not be read.' @(
            'Run OpenAI4S.cmd url to inspect the error.'
        )
    }
    $candidate = $result.Lines | Where-Object {
        $_ -match '^https?://'
    } | Select-Object -Last 1
    $parsed = $null
    if (-not $candidate -or
        -not [Uri]::TryCreate($candidate, [UriKind]::Absolute, [ref] $parsed) -or
        [string]::IsNullOrWhiteSpace($parsed.Query)) {
        Stop-WithGuidance 'OpenAI4S did not return a tokenized browser URL.' @(
            'Read the daemon log inside WSL:',
            "    wsl -d $Distro -- tail -40 $WslLogPath"
        )
    }
    return $parsed.AbsoluteUri
}

function Test-OpenAI4SServing([string] $Distro, [string] $BootstrapLinux, [string] $BundleDir) {
    $result = Invoke-BootstrapCapture $Distro $BootstrapLinux @('cli', $BundleDir, 'status')
    return $result.ExitCode -eq 0
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

function Test-SandboxIndependentCli([string[]] $CliArgs) {
    if (-not $CliArgs -or $CliArgs.Count -eq 0) { return $false }
    # These commands inspect or control the daemon without starting a kernel.
    # In particular, doctor must remain reachable when bubblewrap is the thing
    # the user is trying to diagnose.
    return ([string] $CliArgs[0]) -in @(
        'status', 'url', 'stop', 'doctor', 'diagnostics', 'verify-package',
        '--help', '-h'
    )
}

# ---------------------------------------------------------------------------

# WSL first, package second. Both are fatal, but "you have no WSL2" is the one
# a user has to fix before anything else in this package means anything -- and
# checking it first is also what lets the refusal be exercised against a bare
# checkout, on a runner that has no WSL, without staging a package.
Assert-HttpUrl 'OPENAI4S_WSL_PROXY' $WslProxy
Assert-HttpUrl 'OPENAI4S_WSL_PYPI_INDEX' $PypiIndex
Assert-HttpUrl 'OPENAI4S_WSL_CONDA_MIRROR' $CondaMirror
Assert-WslDataDir $WslDataDir
$parsedPort = 0
if (-not [int]::TryParse($AppPort, [ref] $parsedPort) -or
    $parsedPort -lt 1 -or $parsedPort -gt 65535) {
    Stop-WithGuidance 'OPENAI4S_PORT must be between 1 and 65535.' @(
        "Current value: $AppPort"
    )
}

$distro = Select-Distro
if (-not $env:OPENAI4S_HOST -and (Test-LocalhostForwardingDisabled)) {
    $fallbackHost = Get-WslIpv4 $distro
    if (-not $fallbackHost) {
        Stop-WithGuidance 'localhost forwarding is disabled and the WSL NAT address was not found.' @(
            'Remove localhostForwarding=false from %USERPROFILE%\.wslconfig,',
            'run wsl --shutdown, then start OpenAI4S again.'
        )
    }
    $AppHost = $fallbackHost
    $Url = "http://${AppHost}:${AppPort}/"
    Write-Host "  localhostForwarding=false detected; using WSL NAT address $AppHost." -ForegroundColor Yellow
}
$facts = Get-PackageFacts
$packageLinux = ConvertTo-WslPath $distro $Here
$bootstrap = "$packageLinux/wsl/bootstrap.sh"
$payloadLinux = "$packageLinux/payload/$($facts.PayloadName)"

if (-not (Test-SandboxIndependentCli $Arguments)) {
    $code = Invoke-Bootstrap $distro $bootstrap @('preflight')
    if ($code -ne 0) {
        Stop-WithGuidance 'this WSL distribution cannot provide an isolated kernel.' @(
            'Use WSL2 with Ubuntu 24.04 or newer:',
            '    wsl --install -d Ubuntu-24.04',
            '',
            'Then install bubblewrap inside Ubuntu:',
            '    sudo apt update && sudo apt install -y bubblewrap'
        )
    }
}

# A CLI passthrough must not start anything or open a browser: `OpenAI4S.cmd
# status` is a question, and answering it by launching the daemon would make the
# answer always yes.
if ($Arguments -and $Arguments.Count -gt 0) {
    $code = Invoke-Bootstrap $distro $bootstrap (@('install', $payloadLinux, $facts.Digest, $facts.BundleDir))
    if ($code -ne 0) { exit $code }
    exit (Invoke-Bootstrap $distro $bootstrap (@('cli', $facts.BundleDir) + $Arguments))
}

Write-Section "OpenAI4S $($facts.Version) -- starting in WSL2 ($distro)"

Write-Host '  [1/3] installing the verified Linux bundle (first run only, ~1 GB unpacked)...'
$code = Invoke-Bootstrap $distro $bootstrap (@('install', $payloadLinux, $facts.Digest, $facts.BundleDir))
if ($code -ne 0) {
    Stop-WithGuidance 'the Linux bundle could not be installed into WSL.' @(
        'The message above says why. The usual causes are a full disk inside',
        'the distribution, or the package sitting on a folder WSL cannot read.'
    )
}

if (Test-Serving) {
    if (Test-OpenAI4SServing $distro $bootstrap $facts.BundleDir) {
        $appUrl = Get-AppUrl $distro $bootstrap $facts.BundleDir
        Write-Host "OpenAI4S is already serving at $Url -- opening it." -ForegroundColor Green
        Open-AppUrl $appUrl
        exit 0
    }
    Stop-WithGuidance "port $AppPort is already in use by another program." @(
        'Choose another port and run again, for example:',
        '    set OPENAI4S_PORT=8080',
        '    OpenAI4S.cmd'
    )
}

Write-Host '  [2/3] starting the daemon...'
$code = Invoke-Bootstrap $distro $bootstrap (@('serve', $facts.BundleDir, $AppHost, $AppPort))
if ($code -ne 0) {
    Stop-WithGuidance 'the daemon did not start.' @(
        'Read the log from inside WSL:',
        "    wsl -d $distro -- tail -40 $WslLogPath"
    )
}

Write-Host '  [3/3] waiting for the web UI...'
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    if (Test-Serving) {
        if (Test-OpenAI4SServing $distro $bootstrap $facts.BundleDir) {
            $appUrl = Get-AppUrl $distro $bootstrap $facts.BundleDir
            Write-Host ''
            Write-Host "  ready: $Url" -ForegroundColor Green
            Open-AppUrl $appUrl
            exit 0
        }
    }
    Start-Sleep -Milliseconds 500
}

Stop-WithGuidance "the daemon started but $Url never answered within 60s." @(
    'Read the log from inside WSL:',
    "    wsl -d $distro -- tail -40 $WslLogPath",
    '',
    'If the log looks healthy, WSL localhost forwarding may be off. Check for',
    'a [wsl2] localhostForwarding=false line in %USERPROFILE%\.wslconfig, then',
    '    wsl --shutdown',
    'and run OpenAI4S.cmd again.'
)
