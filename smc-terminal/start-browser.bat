@echo off
setlocal EnableExtensions
title SMC Terminal (mode browser) - tutup jendela ini untuk berhenti
cd /d "%~dp0"
rem Cadangan bila aplikasi desktop tidak bisa dipakai: server berjalan di jendela ini, UI dibuka di browser.
set "WEB_PORT=5080"
call "%~dp0setup.bat" --quiet || exit /b 1
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:%WEB_PORT%/'"
echo  SMC Terminal (mode browser): http://127.0.0.1:%WEB_PORT%/
echo  Tutup jendela ini untuk menghentikan server dan engine Python.
"%~dp0app\SmcTerminal.Gateway.exe" --contentRoot "%~dp0gateway" --urls http://127.0.0.1:%WEB_PORT% --Terminal:ManageEngine=true
pause
