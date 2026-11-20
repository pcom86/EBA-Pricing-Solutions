<#
.SYNOPSIS
    Runs the test suite with coverage using the repo virtual environment.

.EXAMPLE
    .\run_tests.ps1                 # full suite + coverage
    .\run_tests.ps1 -Golden         # golden-master pricing regression only
    .\run_tests.ps1 -Smoke -Html    # include pricing smoke test, write htmlcov\index.html
    .\run_tests.ps1 -All -Lint      # include smoke + Docker tests, then run Ruff
#>
[CmdletBinding()]
param(
    [switch]$Golden,   # only tests marked "golden"
    [switch]$Smoke,    # enable the real pricing smoke test (GAP_RUN_PRICING_SMOKE=1)
    [switch]$Docker,   # enable the Docker runtime test (GAP_RUN_DOCKER_TESTS=1, needs Docker)
    [switch]$All,      # same as -Smoke -Docker
    [switch]$Html,     # also write an HTML coverage report to htmlcov\
    [switch]$Lint,     # also run "ruff check ."
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs  # extra arguments passed straight to pytest
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Virtual environment not found at .venv. Create it and install requirements first."
    exit 1
}

if ($All) { $Smoke = $true; $Docker = $true }

$savedEnv = @{
    GAP_RUN_PRICING_SMOKE = $env:GAP_RUN_PRICING_SMOKE
    GAP_RUN_DOCKER_TESTS  = $env:GAP_RUN_DOCKER_TESTS
}
if ($Smoke)  { $env:GAP_RUN_PRICING_SMOKE = "1" }
if ($Docker) { $env:GAP_RUN_DOCKER_TESTS = "1" }

$cmdArgs = @("-m", "pytest")
if ($Golden) { $cmdArgs += @("-m", "golden", "--no-cov") }
if ($Html)   { $cmdArgs += "--cov-report=html" }
if ($PytestArgs) { $cmdArgs += $PytestArgs }

$exitCode = 0
try {
    Write-Host "> python $($cmdArgs -join ' ')" -ForegroundColor Cyan
    & $python @cmdArgs
    $exitCode = $LASTEXITCODE

    if ($Lint) {
        Write-Host "> ruff check ." -ForegroundColor Cyan
        & $python -m ruff check .
        if ($LASTEXITCODE -ne 0 -and $exitCode -eq 0) { $exitCode = $LASTEXITCODE }
    }
}
finally {
    $env:GAP_RUN_PRICING_SMOKE = $savedEnv.GAP_RUN_PRICING_SMOKE
    $env:GAP_RUN_DOCKER_TESTS  = $savedEnv.GAP_RUN_DOCKER_TESTS
}

if ($Html -and $exitCode -eq 0) {
    Write-Host "Coverage report: $(Join-Path $PSScriptRoot 'htmlcov\index.html')" -ForegroundColor Green
}
if ($exitCode -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
} else {
    Write-Host "Checks failed (exit code $exitCode)." -ForegroundColor Red
}
exit $exitCode
