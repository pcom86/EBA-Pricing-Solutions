<#
.SYNOPSIS
    Build and start the GAP Pricing backend (headless engine) and
    frontend (PySide6 GUI) Docker containers.

.DESCRIPTION
    Backend  : compose service "pricing" (Dockerfile target: headless)
               Runs `python -B -m pricing_engine` as a batch job over
               GAP_HOST_INPUT and exits when the batch completes.
    Frontend : Dockerfile target "runtime" (gap-gui image)
               Runs the PySide6 GUI (gap_pricing_gui.launch_gap_gui)
               detached as container "gap-gui".

    NOTE: The GUI is a desktop Qt app. On Windows Docker Desktop there is
    no X11 display, so the script defaults to QT_QPA_PLATFORM=offscreen:
    the container runs but no window is visible. For a real display on a
    Linux host with X11, pass -X11 and mount the display socket
    (see deployments/GUI_CONTAINER.md). To use the GUI interactively on
    Windows, run it natively instead:
      ..\.venv\Scripts\python.exe -m gap_pricing_gui.launch_gap_gui

.PARAMETER SkipBuild
    Reuse existing images; do not rebuild.

.PARAMETER Offscreen
    Start the GUI container with QT_QPA_PLATFORM=offscreen. This is the
    default on Windows (no X11 display exists); the app runs but no
    window is visible.

.PARAMETER X11
    Use QT_QPA_PLATFORM=xcb for a real display. Only works on a Linux
    host with an X11 server (see deployments/GUI_CONTAINER.md).

.PARAMETER SkipBackend
    Start only the frontend container.

.PARAMETER SkipFrontend
    Run only the backend batch (same as run-local.cmd / compose run).

.EXAMPLE
    .\run-docker-app.ps1
    .\run-docker-app.ps1 -Offscreen
    .\run-docker-app.ps1 -SkipFrontend
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$Offscreen,
    [switch]$X11,
    [switch]$SkipBackend,
    [switch]$SkipFrontend
)

# Continue (not Stop): Docker writes progress/errors to stderr, which Windows
# PowerShell 5.1 would otherwise surface as a terminating NativeCommandError.
# Failures are checked explicitly via $LASTEXITCODE below.
$ErrorActionPreference = "Continue"
$BackendDir = $PSScriptRoot
Set-Location $BackendDir

# --- Mount sources (same overrides as compose.yaml) -------------------------
$HostInput       = if ($env:GAP_HOST_INPUT)       { $env:GAP_HOST_INPUT }       else { Join-Path $BackendDir "Test\Batch run" }
$HostBasis       = if ($env:GAP_HOST_BASIS)       { $env:GAP_HOST_BASIS }       else { Join-Path $BackendDir "basis_snapshots" }
$HostAssumptions = if ($env:GAP_HOST_ASSUMPTIONS) { $env:GAP_HOST_ASSUMPTIONS } else { Join-Path $BackendDir "assumption_snapshots" }
$HostOutput      = if ($env:GAP_HOST_OUTPUT)      { $env:GAP_HOST_OUTPUT }      else { Join-Path $BackendDir "pricing_output" }

$BasisSnapshot       = if ($env:GAP_BASIS_SNAPSHOT)       { $env:GAP_BASIS_SNAPSHOT }       else { "basis_v2026_08" }
$AssumptionSnapshot  = if ($env:GAP_ASSUMPTION_SNAPSHOT)  { $env:GAP_ASSUMPTION_SNAPSHOT }  else { "assumption_A2026_08" }

foreach ($dir in @($HostInput, $HostBasis, $HostAssumptions)) {
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
        throw "Required input folder not found: $dir`nSet GAP_HOST_INPUT / GAP_HOST_BASIS / GAP_HOST_ASSUMPTIONS."
    }
}
if (-not (Test-Path -LiteralPath $HostOutput)) {
    New-Item -ItemType Directory -Path $HostOutput -Force | Out-Null
}

$FrontendImage = "gap-gui:local"
$GuiContainer  = "gap-gui"

# --- Build ------------------------------------------------------------------
if (-not $SkipBuild) {
    Write-Host "==> Building backend image (headless)..." -ForegroundColor Cyan
    docker compose build pricing
    if ($LASTEXITCODE -ne 0) { throw "Backend image build failed (exit $LASTEXITCODE)." }

    if (-not $SkipFrontend) {
        Write-Host "==> Building frontend image (runtime / GUI)..." -ForegroundColor Cyan
        docker build --target runtime -t $FrontendImage .
        if ($LASTEXITCODE -ne 0) { throw "Frontend image build failed (exit $LASTEXITCODE)." }
    }
}

# --- Frontend ----------------------------------------------------------------
if (-not $SkipFrontend) {
    $existing = docker ps -a --format "{{.Names}}" | Select-String -SimpleMatch $GuiContainer
    if ($existing) {
        docker rm -f $GuiContainer | Out-Null
    }

    if ($X11 -and $Offscreen) { throw "Choose either -X11 or -Offscreen, not both." }
    $qtPlatform = if ($X11) { "xcb" } else { "offscreen" }
    Write-Host "==> Starting frontend container '$GuiContainer' (QT_QPA_PLATFORM=$qtPlatform)..." -ForegroundColor Cyan
    docker run -d --name $GuiContainer --init --read-only `
        --cap-drop ALL --security-opt no-new-privileges `
        --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 `
        -e QT_QPA_PLATFORM=$qtPlatform `
        -e GAP_BASIS_SNAPSHOT=$BasisSnapshot `
        -e GAP_ASSUMPTION_SNAPSHOT=$AssumptionSnapshot `
        --mount "type=bind,src=$HostInput,dst=/data/input,readonly" `
        --mount "type=bind,src=$HostBasis,dst=/data/basis,readonly" `
        --mount "type=bind,src=$HostAssumptions,dst=/data/assumptions,readonly" `
        --mount "type=bind,src=$HostOutput,dst=/data/output" `
        $FrontendImage
    if ($LASTEXITCODE -ne 0) { throw "Frontend container failed to start (exit $LASTEXITCODE)." }

    Write-Host "    Container '$GuiContainer' is running: docker logs -f $GuiContainer" -ForegroundColor DarkGray
    Write-Host "    Stop it with: docker stop $GuiContainer" -ForegroundColor DarkGray
}

# --- Backend -----------------------------------------------------------------
if (-not $SkipBackend) {
    Write-Host "==> Running backend batch (pricing engine)..." -ForegroundColor Cyan
    docker compose run --rm -T pricing
    if ($LASTEXITCODE -ne 0) { throw "Backend batch failed (exit $LASTEXITCODE)." }
}

Write-Host "==> Done." -ForegroundColor Green
