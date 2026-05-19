# SnowLink – Windows development launcher
# Usage: .\scripts\run-dev.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Set-Location $Root

# ── Virtual environment ───────────────────────────────────────────────────
$VenvPython = Join-Path $Root "venv\Scripts\python.exe"
$VenvPip    = Join-Path $Root "venv\Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv venv
}

# ── Install backend dependencies ──────────────────────────────────────────
Write-Host "Installing backend dependencies..." -ForegroundColor Cyan
& $VenvPip install -r backend\requirements.txt --quiet

# ── Start server ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting SnowLink at http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
Write-Host ""

& $VenvPython -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
