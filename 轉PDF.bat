@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" ( echo Drag a report .md onto this bat & pause & exit /b )
.venv\Scripts\python.exe 報告轉PDF.py %1
pause
