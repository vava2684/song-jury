@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: drag a song file onto this bat to score it
  pause
  exit /b
)
.venv\Scripts\python.exe song_scorer.py %1 --json "%~dpn1_評分.json"
pause
