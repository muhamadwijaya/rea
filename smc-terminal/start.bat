@echo off
setlocal EnableExtensions
title SMC Terminal
cd /d "%~dp0"
rem Setup otomatis (hanya bekerja bila ada yang perlu diinstall / dibuild), lalu buka aplikasi desktop.
call "%~dp0setup.bat" --quiet || exit /b 1
start "" "%~dp0app\SMCTerminal.exe"
exit /b 0
