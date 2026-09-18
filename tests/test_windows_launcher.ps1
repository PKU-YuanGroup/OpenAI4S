# Native Windows PowerShell 5.1 contracts, without entering any distribution.
param([string] $Source = (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts/windows/openai4s.ps1'))
$ErrorActionPreference = 'Stop'
$parseTokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in @('Select-Distro', 'Get-WslBootstrapArgs', 'ConvertTo-NativeArgument', 'Start-Bootstrap',
                     'Show-RunningBuild', 'Assert-AppPage', 'Invoke-WslCaptureNative', 'Get-WslArkCliPath',
                     'Enter-Utf8NativeOutput', 'Exit-Utf8NativeOutput', 'Invoke-Bootstrap')
}, $false) | ForEach-Object { Invoke-Expression $_.Extent.Text }
function Stop-WithGuidance([string]$Message, $Lines) { throw $Message }
function Test-DistroHasInstall($Name) { return $Name -eq 'Ubuntu-existing' }
$env:OPENAI4S_WSL_DISTRO = ''
function Get-WslDistros { @([pscustomobject]@{Name='docker-desktop';Version=2;IsDefault=$true}) }
$refused = $false
try { Select-Distro | Out-Null } catch { $refused = $_.Exception.Message -match 'no user-owned' }
if (-not $refused) { throw 'infrastructure-only list was not refused' }
function Get-WslDistros { @(
    [pscustomobject]@{Name='docker-desktop';Version=2;IsDefault=$true},
    [pscustomobject]@{Name='Ubuntu-24.04';Version=2;IsDefault=$false},
    [pscustomobject]@{Name='Ubuntu-existing';Version=2;IsDefault=$false}
) }
if ((Select-Distro) -ne 'Ubuntu-existing') { throw 'existing user data lost selection priority' }
$env:OPENAI4S_WSL_DISTRO = 'docker-desktop'
$refused = $false
try { Select-Distro | Out-Null } catch { $refused = $_.Exception.Message -match 'not installed' }
if (-not $refused) { throw 'explicit infrastructure distro was not refused' }
$normal = @(Get-WslBootstrapArgs 'Ubuntu' '/package with spaces/bootstrap.sh' @('preflight'))
if ($normal -contains '-u') { throw 'normal launch must not switch users' }
$prepare = @(Get-WslBootstrapArgs 'Ubuntu' '/package with spaces/bootstrap.sh' @('prepare') 'root')
if ($prepare[2] -ne '-u' -or $prepare[3] -ne 'root') { throw 'preparation did not select root explicitly' }
if ($prepare[-2] -ne '/package with spaces/bootstrap.sh') { throw 'bootstrap path lost argv boundaries' }
if ((ConvertTo-NativeArgument 'two words') -ne '"two words"') { throw 'native argv lost spaces' }
if ((ConvertTo-NativeArgument 'a"b\') -ne '"a\"b\\"') { throw 'native argv lost quote/backslash escaping' }
if ((ConvertTo-NativeArgument '') -ne '""') { throw 'native argv lost an empty argument' }
if ((ConvertTo-NativeArgument '-d') -ne '-d') { throw 'WSL switch must not be quoted' }
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$PassThru)
    if ($FilePath -ne 'wsl.exe' -or $WindowStyle -ne 'Hidden' -or -not $PassThru) {
        throw 'server must retain a hidden WSL process'
    }
    if ($ArgumentList -notlike '*"/package with spaces/bootstrap.sh"*') {
        throw 'background launch lost bootstrap path boundaries'
    }
    return [pscustomobject]@{ HasExited = $false }
}
Start-Bootstrap 'Ubuntu' '/package with spaces/bootstrap.sh' @('serve', 'current') | Out-Null

# Invoke-WslCaptureNative folds stderr into Output on purpose, so a status
# capture is not a JSON document: wsl.exe's NAT localhost-proxy warning must not
# be read as "a different build is running".
$script:told = @()
function Write-Host { param($Object, $ForegroundColor) $script:told += [string] $Object }
function Invoke-BootstrapCapture {
    param($Distro, $BootstrapLinux, $BootstrapArgs)
    return [pscustomobject]@{
        ExitCode = 0
        Lines    = @(
            'wsl: Detected localhost proxy configuration.',
            '{"running":true,"pid":42,"version":"9.9.9","bundle_id":"beef"}'
        )
    }
}
Show-RunningBuild 'Ubuntu' '/b.sh' 'OpenAI4S-9.9.9' 'beef'
if ($script:told.Count -ne 0) { throw 'a warning line was read as a different build' }
Show-RunningBuild 'Ubuntu' '/b.sh' 'OpenAI4S-9.9.9' 'cafe'
if ($script:told.Count -eq 0) { throw 'a genuinely older build was not reported' }
if ((($script:told) -join ' ') -notmatch 'version 9\.9\.9') { throw 'the running version was lost' }

# The page check must not go through the machine's WinINET proxy, and the team
# sign-in page is the application answering too.
$script:proxyDuringCall = 'unset'
[System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy('http://127.0.0.1:7897')
$script:sentinel = [System.Net.WebRequest]::DefaultWebProxy
function Invoke-WebRequest {
    param($Uri, $TimeoutSec, [switch]$UseBasicParsing)
    $script:proxyDuringCall = [System.Net.WebRequest]::DefaultWebProxy
    return [pscustomobject]@{ StatusCode = 200; Content = '<title>OpenAI4S &#8212; sign in</title>' }
}
Assert-AppPage 'http://172.20.0.2:8760/?token=synthetic'
if ($null -ne $script:proxyDuringCall) { throw 'the page check went through the system proxy' }
if ([System.Net.WebRequest]::DefaultWebProxy -ne $script:sentinel) { throw 'the system proxy was not restored' }

# Exercise actual native UTF-8 bytes under an OEM console encoding. This runs
# in Windows CI without WSL and catches the failure a mocked string cannot.
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('openai4s-native-' + [Guid]::NewGuid().ToString('N'))
$savedPath = $env:PATH
$savedEncoding = [Console]::OutputEncoding
$savedArkCli = $env:OPENAI4S_ARKCLI_PATH
try {
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $env:PATH = $testRoot + ';' + $savedPath
    # PowerShell 6+ cannot compile a console application with Add-Type, so the
    # native fixture is Windows PowerShell only -- the host OpenAI4S.cmd runs.
    if ($PSVersionTable.PSEdition -eq 'Desktop') {
        Add-Type -OutputAssembly (Join-Path $testRoot 'wsl.exe') -OutputType ConsoleApplication -TypeDefinition @'
using System;
using System.Text;
public class WslFixture {
    public static int Main(string[] args) {
        byte[] bytes = Encoding.UTF8.GetBytes("/mnt/c/Users/\u4e2d\u6587 folder\n");
        Console.OpenStandardOutput().Write(bytes, 0, bytes.Length);
        byte[] warning = Encoding.UTF8.GetBytes("wsl: harmless diagnostic\n");
        Console.OpenStandardError().Write(warning, 0, warning.Length);
        return args.Length > 0 && args[0] == "fail" ? 7 : 0;
    }
}
'@
        [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437)
        $expectedPath = '/mnt/c/Users/' + [char]0x4e2d + [char]0x6587 + ' folder'
        foreach ($mode in @('ok', 'fail')) {
            $result = Invoke-WslCaptureNative @($mode)
            $expectedCode = if ($mode -eq 'fail') { 7 } else { 0 }
            if ($result.ExitCode -ne $expectedCode) { throw 'native exit code was lost' }
            if ($result.Output -notcontains $expectedPath) { throw 'native UTF-8 path was corrupted' }
            if ([Console]::OutputEncoding.CodePage -ne 437) { throw 'console encoding was not restored' }
            if ($ErrorActionPreference -ne 'Stop') { throw 'error preference was not restored' }
        }
        # The streaming path (preflight, install, CLI passthrough) decodes too.
        $script:told = @()
        if ((Invoke-Bootstrap 'Ubuntu' '/b.sh' @('preflight')) -ne 0) { throw 'streamed exit code was lost' }
        if ($script:told -notcontains $expectedPath) { throw 'streamed native UTF-8 path was corrupted' }
        if ([Console]::OutputEncoding.CodePage -ne 437) { throw 'console encoding was not restored after streaming' }
        if ($ErrorActionPreference -ne 'Stop') { throw 'error preference was not restored after streaming' }
    } else {
        Write-Output "PowerShell $($PSVersionTable.PSVersion): native console fixture skipped (Windows PowerShell only)"
    }

    # Get-Command resolves an Application by name and PATHEXT, not by content.
    New-Item -ItemType File -Path (Join-Path $testRoot 'arkcli.exe') | Out-Null
    New-Item -ItemType File -Path (Join-Path $testRoot 'arkcli.cmd') | Out-Null
    # Brackets are legal in a folder name and wildcards to Get-Command.
    $bracketCli = Join-Path (Join-Path $testRoot 'Tools [x64]') 'arkcli.exe'
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($bracketCli)) | Out-Null
    [IO.File]::WriteAllBytes($bracketCli, [byte[]] @())
    $script:translationFails = $false
    function ConvertTo-WslPath($Distro, $WindowsPath, [switch] $Optional) {
        if ($WindowsPath -notin @((Join-Path $testRoot 'arkcli.exe'), $bracketCli)) { throw 'wrong Windows CLI selected' }
        if (-not $Optional) { throw 'an Ark CLI translation failure must not use the package guidance' }
        if ($script:translationFails) { return '' }
        return '/windows tools/arkcli.exe'
    }
    $env:OPENAI4S_ARKCLI_PATH = ''
    $WslArkCli = Get-WslArkCliPath 'Ubuntu'
    if ($WslArkCli -ne '/windows tools/arkcli.exe') { throw 'Windows PATH CLI was not discovered' }
    $forwarded = @(Get-WslBootstrapArgs 'Ubuntu' '/b.sh' @('serve'))
    # Discovered, not chosen: a CLI installed inside WSL must keep precedence.
    if ($forwarded -notcontains 'OPENAI4S_ARKCLI_FALLBACK_PATH=/windows tools/arkcli.exe') { throw 'CLI path lost argv boundary' }
    if ($forwarded -like 'OPENAI4S_ARKCLI_PATH=*') { throw 'a discovered Windows CLI overrode the WSL CLI' }
    function Start-Process {
        param($FilePath, $ArgumentList, $WindowStyle, [switch]$PassThru)
        $script:nativeLine = $ArgumentList
        return [pscustomobject]@{ HasExited = $false }
    }
    Start-Bootstrap 'Ubuntu' '/b.sh' @('serve') | Out-Null
    if ($script:nativeLine -notlike '*"OPENAI4S_ARKCLI_FALLBACK_PATH=/windows tools/arkcli.exe"*') { throw 'CLI path lost its native argv boundary' }

    # An optional CLI WSL cannot reach is skipped; a configured one is refused.
    $script:translationFails = $true
    if ((Get-WslArkCliPath 'Ubuntu') -ne '') { throw 'an unreachable PATH CLI was forwarded' }
    $env:OPENAI4S_ARKCLI_PATH = Join-Path $testRoot 'arkcli.exe'
    $refused = $false
    try { Get-WslArkCliPath 'Ubuntu' | Out-Null } catch { $refused = $_.Exception.Message -match 'cannot reach' }
    if (-not $refused) { throw 'an unreachable configured CLI was not refused' }
    $script:translationFails = $false

    $env:OPENAI4S_ARKCLI_PATH = '/opt/ark/bin/arkcli'
    if ((Get-WslArkCliPath 'Ubuntu') -ne '/opt/ark/bin/arkcli') { throw 'explicit WSL path lost precedence' }
    $env:OPENAI4S_ARKCLI_PATH = Join-Path $testRoot 'arkcli.exe'
    $WslArkCli = Get-WslArkCliPath 'Ubuntu'
    if ($WslArkCli -ne '/windows tools/arkcli.exe') { throw 'explicit Windows path was not translated' }
    if (@(Get-WslBootstrapArgs 'Ubuntu' '/b.sh' @('serve')) -notcontains 'OPENAI4S_ARKCLI_PATH=/windows tools/arkcli.exe') {
        throw 'an explicit Windows CLI was not forwarded as the override'
    }
    $env:OPENAI4S_ARKCLI_PATH = $bracketCli
    if ((Get-WslArkCliPath 'Ubuntu') -ne '/windows tools/arkcli.exe') { throw 'a bracketed Windows path was read as a wildcard' }
    $npmDir = Join-Path $testRoot 'npm'
    [IO.Directory]::CreateDirectory($npmDir) | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $npmDir 'arkcli.cmd'), [byte[]] @())
    $env:PATH = $npmDir + ';' + $testRoot + ';' + $savedPath
    $env:OPENAI4S_ARKCLI_PATH = 'arkcli'
    if ((Get-WslArkCliPath 'Ubuntu') -ne '/windows tools/arkcli.exe') { throw 'an earlier npm .cmd hid arkcli.exe' }
    $env:PATH = $testRoot + ';' + $savedPath
    foreach ($invalid in @((Join-Path $testRoot 'missing\arkcli.exe'), (Join-Path $testRoot 'arkcli.cmd'), 'arkcli*', '*')) {
        $env:OPENAI4S_ARKCLI_PATH = $invalid
        $refused = $false
        try { Get-WslArkCliPath 'Ubuntu' | Out-Null } catch { $refused = $_.Exception.Message -match 'does not name' }
        if (-not $refused) { throw "an unusable configured CLI was accepted: $invalid" }
    }
    $env:OPENAI4S_ARKCLI_PATH = ''
    $env:PATH = Join-Path $testRoot 'no-such-dir'
    if ((Get-WslArkCliPath 'Ubuntu') -ne '') { throw 'a missing optional CLI was not ignored' }
} finally {
    # Best effort, so a cleanup failure never replaces the assertion that failed.
    $env:PATH = $savedPath
    $env:OPENAI4S_ARKCLI_PATH = $savedArkCli
    [Console]::OutputEncoding = $savedEncoding
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedTestRoot.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "PowerShell $($PSVersionTable.PSVersion): launcher contracts passed"
