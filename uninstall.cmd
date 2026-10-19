@echo off
set "SUNHUB_ARGS="
:loop
if "%~1"=="" goto run
if /I "%~1"=="--keep-data" set "SUNHUB_ARGS=%SUNHUB_ARGS% -KeepData"
if /I "%~1"=="--yes" set "SUNHUB_ARGS=%SUNHUB_ARGS% -Yes"
if /I "%~1"=="-y" set "SUNHUB_ARGS=%SUNHUB_ARGS% -Yes"
shift
goto loop
:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %SUNHUB_ARGS%
pause
