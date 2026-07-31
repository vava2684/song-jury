@echo off
chcp 65001 >nul
title song-jury installer
cd /d "%~dp0"
where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
)
if errorlevel 1 pause
