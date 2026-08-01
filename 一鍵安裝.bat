@echo off
chcp 65001 >nul
title song-jury installer
cd /d "%~dp0"
rem ---------------------------------------------------------------
rem  IMPORTANT: keep the child's exit code.
rem  The old version ran `if errorlevel 1 pause` as the LAST command,
rem  so this .bat returned pause's own code (0) and any installer
rem  failure (1 / 3 / 5) was silently reported as success to callers.
rem  Save %errorlevel% right after the call, pause only for humans,
rem  then exit /b with the saved code.  (Codex R16-11)
rem ---------------------------------------------------------------
where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
)
set "rc=%errorlevel%"
if not "%rc%"=="0" pause
exit /b %rc%
