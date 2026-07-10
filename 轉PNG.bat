@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" ( echo Drag a PDF onto this bat & pause & exit /b )
.venv\Scripts\python.exe 轉PNG.py %1
pause
