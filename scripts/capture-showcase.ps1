[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [ValidateRange(30, 600)]
    [int]$StartupTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$runner = Join-Path $PSScriptRoot "run-e2e.ps1"
$runnerParameters = @{
    Task = "showcase:capture"
    StartupTimeoutSeconds = $StartupTimeoutSeconds
}
if ($SkipBuild.IsPresent) {
    $runnerParameters.SkipBuild = $true
}

& $runner @runnerParameters
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$assetDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) "docs/assets/showcase"
Write-Host "Showcase assets generated under $assetDirectory"
