@echo off
set "SUNHUB_ARGS="
if /I "%~1"=="--purge" set "SUNHUB_ARGS=-Purge"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %SUNHUB_ARGS%
pause
