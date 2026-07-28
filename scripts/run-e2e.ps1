[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$KeepEnvironment,
    [ValidateRange(30, 600)]
    [int]$StartupTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$projectName = "alert-sage-e2e"
$composeFiles = @("-f", "docker-compose.yml", "-f", "docker-compose.demo.yml")
$environmentOverrides = @{
    ALERT_SAGE_BUILD_REVISION = $null
    ALERT_SAGE_E2E_BASE_URL = "http://127.0.0.1:25173"
    POSTGRES_PORT = "25432"
    REDIS_PORT = "26379"
    API_PORT = "28000"
    WEB_PORT = "25173"
    WORKER_METRICS_PORT = "29101"
    PROMETHEUS_PORT = "29090"
    GRAFANA_PORT = "23000"
}
$previousEnvironment = @{}
$environmentCaptured = $false
$environmentTouched = $false
$environmentStartAttempted = $false
$logsSaved = $false

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    & docker compose --project-name $projectName @composeFiles @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose command failed: $($Arguments -join ' ')"
    }
}

function Save-ComposeLogs {
    $artifactDirectory = Join-Path $workspaceRoot "test-results"
    New-Item -ItemType Directory -Path $artifactDirectory -Force | Out-Null
    $logPath = Join-Path $artifactDirectory "compose.log"
    $logs = & docker compose --project-name $projectName @composeFiles logs --no-color 2>&1
    [System.IO.File]::WriteAllLines($logPath, [string[]]$logs)
    $script:logsSaved = $true
    Write-Host "Compose logs saved to $logPath"
}

function Remove-E2EEnvironment {
    if ($projectName -ne "alert-sage-e2e") {
        throw "Refusing to remove an unexpected Compose project: $projectName"
    }
    & docker compose --project-name $projectName @composeFiles down --volumes --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Unable to fully remove the isolated E2E environment."
    }
}

try {
    Set-Location -LiteralPath $workspaceRoot
    if (-not (Test-Path -LiteralPath (Join-Path $workspaceRoot "node_modules/.bin/playwright.cmd"))) {
        throw "Playwright is not installed. Run 'npm ci' and 'npm run e2e:install' first."
    }

    foreach ($name in $environmentOverrides.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
    }
    $environmentCaptured = $true

    $revision = (& git rev-parse --short=12 HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $revision) {
        throw "Unable to resolve the current Git revision."
    }
    $worktreeChanges = & git status --porcelain
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the current Git worktree."
    }
    if ($worktreeChanges) {
        $revision = "$revision-dirty"
    }
    $environmentOverrides.ALERT_SAGE_BUILD_REVISION = $revision
    foreach ($entry in $environmentOverrides.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value)
    }

    $environmentTouched = $true
    Remove-E2EEnvironment

    $upArguments = @("up", "-d")
    if (-not $SkipBuild) {
        $upArguments += "--build"
    }
    $upArguments += @("postgres", "redis", "api", "worker", "relay", "web")
    $environmentStartAttempted = $true
    Invoke-Compose @upArguments

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $ready = $false
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:28000/api/v1/health/ready" -TimeoutSec 3
            if ($response.status -eq "ok") {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) {
        throw "The isolated E2E API did not become ready within $StartupTimeoutSeconds seconds."
    }

    & npm run e2e
    if ($LASTEXITCODE -ne 0) {
        Save-ComposeLogs
        throw "Playwright E2E tests failed."
    }
}
catch {
    if ($environmentStartAttempted -and -not $logsSaved) {
        Save-ComposeLogs
    }
    if (Test-Path -LiteralPath (Join-Path $workspaceRoot "test-results")) {
        Write-Host "Playwright failure artifacts are under test-results/."
    }
    throw
}
finally {
    if ($environmentTouched -and -not $KeepEnvironment) {
        Remove-E2EEnvironment
    }
    if ($environmentCaptured) {
        foreach ($name in $environmentOverrides.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name])
        }
    }
}
