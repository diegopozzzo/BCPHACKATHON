# Arranque local: Evolution (Docker) + FastAPI (uvicorn).
# Ejecutar desde la raíz del repo: .\scripts\start-dev.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

Write-Host "Docker compose (Evolution)..." -ForegroundColor Cyan
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "docker compose falló. ¿Docker Desktop encendido?" -ForegroundColor Red
    exit 1
}

$venvPy = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "No existe backend\.venv. Ejecuta: cd backend; python -m venv .venv; .\.venv\Scripts\activate; pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

Write-Host "Uvicorn http://0.0.0.0:8000 (Ctrl+C para salir)..." -ForegroundColor Cyan
Set-Location (Join-Path $root "backend")
& $venvPy -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
