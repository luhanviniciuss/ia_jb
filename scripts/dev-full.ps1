$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot

Write-Host "[dev:full] Limpando backend antigo na porta 8899..."
$listening = netstat -ano | Select-String ":8899" | Where-Object { $_.Line -match "LISTENING" }
if ($listening) {
  foreach ($entry in $listening) {
    $parts = ($entry.Line -split "\s+") | Where-Object { $_ -ne "" }
    $oldPid = $parts[-1]
    try {
      Stop-Process -Id $oldPid -Force -ErrorAction Stop
      Write-Host "[dev:full] Processo antigo encerrado: PID $oldPid"
    } catch {
      Write-Host "[dev:full] Nao foi possivel encerrar PID $oldPid"
    }
  }
}

Write-Host "[dev:full] Subindo backend (FastAPI) na 8899..."
$backend = Start-Process python -ArgumentList "backend/main.py" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 2

try {
  $health = Invoke-WebRequest -Uri "http://127.0.0.1:8899/api/health" -UseBasicParsing -TimeoutSec 6
  Write-Host "[dev:full] Backend OK: $($health.StatusCode)"
} catch {
  Write-Host "[dev:full] Backend nao respondeu no healthcheck inicial."
}

Write-Host "[dev:full] Subindo frontend (Vite) na 5000..."
try {
  cmd /c npm run dev:web
} finally {
  if ($backend -and -not $backend.HasExited) {
    Stop-Process -Id $backend.Id -Force
    Write-Host "[dev:full] Backend encerrado."
  }
}
