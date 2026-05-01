# Valida coherencia local tras reinicio (Docker + FastAPI + Evolution).
# Ejecutar desde cualquier cwd: .\scripts\validate-stack.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$issues = @()
$ok = @()

function Test-TcpPort([string]$HostName, [int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $ia = $c.ConnectAsync($HostName, $Port)
        $ia.Wait(2000) | Out-Null
        $r = $c.Connected
        $c.Close()
        return $r
    } catch { return $false }
}

$dockerOk = $false
try {
    docker info 2>&1 | Out-Null
    $dockerOk = ($LASTEXITCODE -eq 0)
} catch { $dockerOk = $false }

if ($dockerOk) {
    $ok += "Docker Engine responde."
    $names = docker ps --format "{{.Names}}" 2>$null
    if ($names -match "bcp_evolution_api") { $ok += "Contenedor bcp_evolution_api en ejecución." }
    else { $issues += "Stack BCP: falta bcp_evolution_api. En la raíz: docker compose up -d" }
} else {
    $issues += "Docker no responde: abre Docker Desktop y espera a 'Engine running'."
}

if (Test-TcpPort "127.0.0.1" 8080) { $ok += "Puerto 8080 (Evolution) abierto." }
else { $issues += "Puerto 8080 cerrado (sin Evolution en el host)." }

if (Test-TcpPort "127.0.0.1" 8000) { $ok += "Puerto 8000 (FastAPI) abierto." }
else { $issues += "Puerto 8000 cerrado (sin uvicorn)." }

$envPath = Join-Path $root ".env"
if (Test-Path $envPath) {
    $lines = Get-Content $envPath
    $evKey = ($lines | Where-Object { $_ -match '^\s*EVOLUTION_API_KEY=' } | Select-Object -First 1) -replace '^\s*EVOLUTION_API_KEY=', '' -replace '^"|"$', ''
    $authKey = ($lines | Where-Object { $_ -match '^\s*AUTHENTICATION_API_KEY=' } | Select-Object -First 1) -replace '^\s*AUTHENTICATION_API_KEY=', '' -replace '^"|"$', ''
    $inst = ($lines | Where-Object { $_ -match '^\s*EVOLUTION_INSTANCE=' } | Select-Object -First 1) -replace '^\s*EVOLUTION_INSTANCE=', '' -replace '^"|"$', ''
    if ($evKey -and $authKey -and $evKey -eq $authKey) {
        $ok += "EVOLUTION_API_KEY y AUTHENTICATION_API_KEY coinciden."
    } elseif ($evKey -and $authKey) {
        $issues += "EVOLUTION_API_KEY y AUTHENTICATION_API_KEY difieren."
    }
    if ($inst) { $ok += "EVOLUTION_INSTANCE=$inst" }
} else {
    $issues += "No existe .env en la raíz del repo."
}

Write-Host "`n=== Validación BCP_HACKATHON ($root) ===" -ForegroundColor Cyan
foreach ($m in $ok) { Write-Host "  [OK] $m" -ForegroundColor Green }
foreach ($m in $issues) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
Write-Host ""
if ($issues.Count -eq 0) { exit 0 } else { exit 1 }
