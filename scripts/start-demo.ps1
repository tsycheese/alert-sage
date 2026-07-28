[CmdletBinding()]
param(
    [switch]$Approve,
    [switch]$SkipBuild,
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 120,
    [string]$WebBaseUrl = "http://localhost:15173"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$previousRevision = [Environment]::GetEnvironmentVariable("ALERT_SAGE_BUILD_REVISION")
$composeFiles = @("-f", "docker-compose.yml", "-f", "docker-compose.demo.yml")

try {
    Set-Location -LiteralPath $workspaceRoot
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
    $env:ALERT_SAGE_BUILD_REVISION = $revision

    $upArguments = @("compose") + $composeFiles + @("up", "-d")
    if (-not $SkipBuild) {
        $upArguments += "--build"
    }
    $upArguments += @("postgres", "redis", "api", "worker", "relay", "web")
    & docker @upArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to start the demo environment."
    }

    $bootstrapArguments = @(
        "compose"
    ) + $composeFiles + @(
        "exec", "-T", "api",
        ".venv/bin/python", "-m", "scripts.bootstrap_demo",
        "--base-url", "http://127.0.0.1:8000",
        "--web-base-url", $WebBaseUrl,
        "--timeout-seconds", $TimeoutSeconds,
        "--expect-failed-tool", "logs"
    )
    if ($Approve) {
        $bootstrapArguments += "--approve"
    }
    & docker @bootstrapArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Demo bootstrap failed. Inspect API, Worker and Relay logs for details."
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        "ALERT_SAGE_BUILD_REVISION",
        $previousRevision
    )
}
