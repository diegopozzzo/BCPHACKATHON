# Expone el backend FastAPI (puerto por defecto 8000) por HTTPS para webhooks públicos.
# 1) Authtoken (una vez, no lo subas a git):
#    ngrok config add-authtoken TU_TOKEN
# 2) Ejecuta desde la raíz del repo:
#    .\scripts\ngrok-backend.ps1
# 3) Copia la URL https de la consola de ngrok y configura en Evolution:
#    POST http://localhost:8080/webhook/set/rutepe
#    body: { "webhook": { "url": "https://xxxx.ngrok-free.app/webhooks/evolution", ... } }
param(
    [int]$Port = 8000
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Write-Host "Iniciando tunel ngrok http $Port (FastAPI). Cierra con Ctrl+C." -ForegroundColor Cyan
ngrok http $Port
