@echo off
rem Sunhub no Windows: prepara (na primeira vez) e abre o app. Uso: app.cmd  [--install]
set "SUNHUB_ARGS="
if /I "%~1"=="--install" set "SUNHUB_ARGS=-Install"
if /I "%~1"=="-i" set "SUNHUB_ARGS=-Install"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app.ps1" %SUNHUB_ARGS%
if errorlevel 1 pause
