@echo off
setlocal
cd /d "%~dp0"
set PYTHONPATH=%~dp0
if not exist ".venv\Scripts\python.exe" (
  echo [simulcast] venv missing: run  uv venv .venv  then  uv pip install -r requirements.txt
  exit /b 1
)
set SRC=%1
if "%SRC%"=="" set SRC=loopback
echo [simulcast] make sure your LLM endpoints are up (scripts\serve-local.sh or cloud profile)
if /I "%SRC%"=="both" echo [simulcast] loopback + microphone
echo [simulcast] starting subtitle renderer...
start "" ".venv\Scripts\pythonw.exe" -m simulcast.render.qt --ws ws://127.0.0.1:8765
echo [simulcast] capturing %SRC% (Ctrl+C to stop)
".venv\Scripts\python.exe" -m simulcast.cli run --live %SRC% --ws
echo [simulcast] pipeline stopped
