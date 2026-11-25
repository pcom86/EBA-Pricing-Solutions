<#
.SYNOPSIS
    Runs all static checks and tests: Ruff, mypy, and the pytest suite.

.DESCRIPTION
    - Ruff runs from the repo root (pyproject.toml [tool.ruff]).
    - mypy must run inside BackendEngine\ (namespace-package layout);
      config lives in pyproject.toml [tool.mypy].
    - pytest runs tests/unit and BackendEngine/tests.

.EXAMPLE
    .\run_checks.ps1            # ruff + mypy + all tests
    .\run_checks.ps1 -SkipTests # ruff + mypy only
#>
[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Virtual environment not found at .venv. Create it and install requirements first."
    exit 1
}

$failed = @()

Write-Host "> ruff check ." -ForegroundColor Cyan
& $python -m ruff check .
if ($LASTEXITCODE -ne 0) { $failed += "ruff" }

Write-Host "> mypy BackendEngine" -ForegroundColor Cyan
Push-Location (Join-Path $PSScriptRoot "BackendEngine")
try {
    & $python -m mypy .
    if ($LASTEXITCODE -ne 0) { $failed += "mypy" }
}
finally {
    Pop-Location
}

if (-not $SkipTests) {
    Write-Host "> vulture BackendEngine (dead code)" -ForegroundColor Cyan
    # ignore-names covers intentional cases: signal-handler params, reserved API
    # params, and deliberate warm-up imports of the reporting package.
    & $python -m vulture `
        BackendEngine/ctr_loader BackendEngine/pricing_engine BackendEngine/reporting `
        BackendEngine/gap_config BackendEngine/gap_pricing_gui --min-confidence 80 `
        --ignore-names "signum,frame,scheme_params_override,overwrite,reporting"
    if ($LASTEXITCODE -ne 0) { $failed += "vulture" }

    Write-Host "> bandit BackendEngine (security)" -ForegroundColor Cyan
    # -ll reports medium+ severity only; -s B101 skips assert-used (intentional style here)
    & $python -m bandit -r BackendEngine -x BackendEngine/tests -s B101 -ll -q
    if ($LASTEXITCODE -ne 0) { $failed += "bandit" }

    Write-Host "> pytest tests/unit BackendEngine/tests" -ForegroundColor Cyan
    & $python -m pytest tests/unit BackendEngine/tests -q
    if ($LASTEXITCODE -ne 0) { $failed += "pytest" }
}

if ($failed.Count -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
    exit 0
}
Write-Host "Failed: $($failed -join ', ')" -ForegroundColor Red
exit 1
