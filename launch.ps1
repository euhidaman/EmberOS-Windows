#Requires -Version 5.1
<#
.SYNOPSIS
    Launch EmberOS-Windows: start the service and system tray icon.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Definition
$venvPython = "$ROOT\env\venv\Scripts\python.exe"
$logDir = "$ROOT\logs"
$launchLog = "$logDir\launch.log"

# Ensure log dir exists
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# Check venv exists
if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: Venv Python not found at $venvPython" -ForegroundColor Red
    Write-Host "Run setup.ps1 first."
    exit 1
}

# Check if service is installed
$svcName = "EmberOSAgent"
$svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue

if (-not $svc) {
    Write-Host "EmberOS service not installed — installing now..."
    & $venvPython -m emberos.service install 2>&1
    Start-Sleep -Seconds 2
    $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
}

# Start service
if ($svc) {
    if ($svc.Status -ne "Running") {
        Write-Host "Starting EmberOS service..."
        try {
            Start-Service -Name $svcName
            Write-Host "Service started." -ForegroundColor Green
        } catch {
            Write-Host "Could not start service (may need Admin privileges)." -ForegroundColor Yellow
            Write-Host "Starting agent in standalone mode instead..."
            Start-Process -FilePath $venvPython -ArgumentList "-m emberos.service" -WindowStyle Hidden
        }
    } else {
        Write-Host "EmberOS service is already running."
    }
} else {
    Write-Host "Service registration failed — starting agent in standalone mode..."
    Start-Process -FilePath $venvPython -ArgumentList "-m emberos.service" -WindowStyle Hidden
}

# Wait for service/agent to initialize
Write-Host "Waiting for agent to initialize..."
Start-Sleep -Seconds 5

# Launch tray app
Write-Host "Launching system tray icon..."
Start-Process -FilePath $venvPython -ArgumentList "-m emberos.tray" -WindowStyle Hidden

# Log launch
Add-Content -Path $launchLog -Value "$timestamp - EmberOS launched"

Write-Host ""
Write-Host "EmberOS-Windows is running!" -ForegroundColor Green
Write-Host "  - System tray icon should appear in your taskbar"
Write-Host "  - Use 'emberos.bat chat' for terminal interaction"
Write-Host "  - Use 'emberos.bat status' to check status"
