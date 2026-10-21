@echo off
setlocal
cd /d "%~dp0"
set PYTHONPATH=%~dp0
if not exist ".venv\Scripts\pythonw.exe" (
  echo [simulcast] venv missing: run  uv venv .venv  then  uv pip install -r requirements.txt
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m simulcast.app
