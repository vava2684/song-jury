@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: drag a song file onto this bat
  pause
  exit /b
)
.venv-ml\Scripts\python.exe 評審團.py %1
pause
