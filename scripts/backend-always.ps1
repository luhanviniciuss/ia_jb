$ErrorActionPreference = "Continue"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot

function Get-ListeningPidOnPort([int]$port) {
  $line = netstat -ano | Select-String (":" + $port) | Where-Object { $_.Line -match "LISTENING" } | Select-Object -First 1
  if (-not $line) { return $null }
  $parts = ($line.Line -split "\s+") | Where-Object { $_ -ne "" }
  if ($parts.Count -lt 5) { return $null }
  return $parts[-1]
}

Write-Host "[backend:always] Monitor ativo na porta 8899."

while ($true) {
  $backendPid = Get-ListeningPidOnPort 8899
  if (-not $backendPid) {
    Write-Host "[backend:always] Backend offline. Iniciando python backend/main.py ..."
    Start-Process python -ArgumentList "backend/main.py" -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 3
  }
  Start-Sleep -Seconds 5
}
