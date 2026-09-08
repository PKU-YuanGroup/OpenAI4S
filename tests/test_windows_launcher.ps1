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
                     'Show-RunningBuild', 'Assert-AppPage')
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

Write-Output "PowerShell $($PSVersionTable.PSVersion): launcher contracts passed"
