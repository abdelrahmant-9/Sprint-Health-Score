@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "OUT_LOG=%~dp0hourly.out.log"
set "ERR_LOG=%~dp0hourly.err.log"

if not exist "%PYTHON_EXE%" (
  echo Python environment not found at:
  echo %PYTHON_EXE%
  pause
  exit /b 1
)

start "Sprint Health Hourly Dashboard" /min powershell -NoProfile -WindowStyle Hidden -Command ^
  "& { Set-Location -LiteralPath '%~dp0'; & '%PYTHON_EXE%' -m app.main --mode watch --interval 3600 --format html 1>>'%OUT_LOG%' 2>>'%ERR_LOG%' }"

echo Hourly dashboard refresh started in background.
echo Admin dashboard will auto-start if needed.
echo HTML report will refresh every 1 hour.
exit /b 0
