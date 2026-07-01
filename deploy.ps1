# =============================================================
# Deploy script — descarca ultimul cod de pe GitHub si porneste bot
# Usage: dublu-click sau: powershell -File deploy.ps1
# =============================================================

Write-Host "=== Deploy bot MT5 — start ===" -ForegroundColor Cyan

$botDir = "C:\Users\Administrator\Desktop\bot"
$zipUrl = "https://github.com/dancarlova-atpsor/mt5-trading-bot/archive/refs/heads/main.zip"
$zipPath = "$env:TEMP\bot-latest.zip"
$extractPath = "$env:TEMP\bot-extracted"

Write-Host "1. Curatare temp..." -ForegroundColor Yellow
if (Test-Path $extractPath) { Remove-Item $extractPath -Recurse -Force }

Write-Host "2. Download zip de pe GitHub..." -ForegroundColor Yellow
Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
Write-Host "   OK ($((Get-Item $zipPath).Length / 1KB) KB descarcati)" -ForegroundColor Green

Write-Host "3. Extragere..." -ForegroundColor Yellow
Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

Write-Host "4. Copy fisiere in bot dir (pastreaza .venv, logs, .env)..." -ForegroundColor Yellow
$source = "$extractPath\mt5-trading-bot-main"
robocopy $source $botDir /E /XD .venv logs __pycache__ .git venv /XF .env /R:1 /W:1 | Out-Null
Write-Host "   OK" -ForegroundColor Green

Write-Host "5. Verificare fisiere actualizate..." -ForegroundColor Yellow
Set-Location $botDir
$updatedFiles = Get-Item config\settings.yaml, config\strategies.yaml, config\risk_management.yaml, main.py, risk\risk_manager.py
$updatedFiles | ForEach-Object { Write-Host "   $($_.Name): $($_.LastWriteTime)" -ForegroundColor Gray }

Write-Host "6. Oprire bot vechi..." -ForegroundColor Yellow
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
$stillRunning = Get-Process python -ErrorAction SilentlyContinue
if ($stillRunning) {
    Write-Host "   AVERTISMENT: Python inca ruleaza!" -ForegroundColor Red
} else {
    Write-Host "   OK — bot vechi oprit" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Deploy COMPLET. Pornire bot nou... ===" -ForegroundColor Cyan
Write-Host "IMPORTANT: LASA ACEASTA FEREASTRA DESCHISA! Botul ruleaza aici." -ForegroundColor Yellow
Write-Host ""

# Porneste bot in acelasi PowerShell (nu ca proces separat, ca sa vezi log-urile)
& "$botDir\.venv\Scripts\python.exe" "$botDir\main.py"
