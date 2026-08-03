$b = "C:\Users\Administrator\Desktop\bot"
Write-Host "1/5 Stop bot vechi..." -ForegroundColor Yellow
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Write-Host "2/5 Download cod nou..." -ForegroundColor Yellow
Invoke-WebRequest "https://github.com/dancarlova-atpsor/mt5-trading-bot/archive/refs/heads/main.zip" -OutFile "$env:TEMP\b.zip"
Write-Host "3/5 Extract..." -ForegroundColor Yellow
if (Test-Path "$env:TEMP\bn") { Remove-Item "$env:TEMP\bn" -Recurse -Force }
Expand-Archive "$env:TEMP\b.zip" "$env:TEMP\bn" -Force
Write-Host "4/5 Copy in bot dir (pastreaza .venv, logs, .env)..." -ForegroundColor Yellow
robocopy "$env:TEMP\bn\mt5-trading-bot-main" $b /E /XD .venv logs __pycache__ .git venv /XF .env | Out-Null
Write-Host "5/5 Start bot nou..." -ForegroundColor Green
Set-Location $b
& "$b\.venv\Scripts\python.exe" "$b\main.py"
