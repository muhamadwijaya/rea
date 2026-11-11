@echo off
setlocal EnableExtensions
title SMC Terminal - Launcher
cd /d "%~dp0"

rem ==== Konfigurasi port (ubah bila bentrok) ====
set "ENGINE_PORT=8765"
set "WEB_PORT=5080"
set "ROOT=%~dp0"

echo.
echo  ==========================================================
echo    SMC TERMINAL  -  Screener Saham ^& Forex (Smart Money)
echo    Backend: Python (analisa SMC) + C# (gateway realtime)
echo  ==========================================================
echo.

rem ---------------------------------------------------------------- Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :no_python
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo  [OK] %%v

rem ---------------------------------------------------------------- .NET
where dotnet >nul 2>nul || goto :no_dotnet
dotnet --list-sdks 2>nul | findstr /r /c:"^[89]\." /c:"^1[0-9]\." >nul || goto :no_dotnet
for /f "delims=" %%v in ('dotnet --version') do echo  [OK] .NET SDK %%v

rem ---------------------------------------------------------------- sudah berjalan?
netstat -ano | findstr /r /c:":%WEB_PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo  [i] Port %WEB_PORT% sudah dipakai - kemungkinan SMC Terminal sudah berjalan.
  echo      Jalankan stop.bat dulu bila ingin restart. Membuka browser...
  start "" "http://localhost:%WEB_PORT%/"
  exit /b 0
)

rem ---------------------------------------------------------------- venv + dependensi Python
if not exist "%ROOT%engine\.venv\Scripts\python.exe" (
  echo  [*] Membuat virtualenv Python (sekali saja^)...
  %PY% -m venv "%ROOT%engine\.venv" || goto :fail_venv
)
set "VPY=%ROOT%engine\.venv\Scripts\python.exe"
fc /b "%ROOT%engine\requirements.txt" "%ROOT%engine\.venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
  echo  [*] Menginstall dependensi Python: fastapi, uvicorn, yfinance, pandas, numpy ...
  "%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip
  "%VPY%" -m pip install --disable-pip-version-check -r "%ROOT%engine\requirements.txt" || goto :fail_pip
  copy /y "%ROOT%engine\requirements.txt" "%ROOT%engine\.venv\requirements.installed" >nul
)
echo  [OK] Dependensi Python siap

rem ---------------------------------------------------------------- build C#
echo  [*] Build gateway C# ...
dotnet build "%ROOT%gateway\SmcTerminal.Gateway.csproj" -c Release -nologo -v q || goto :fail_build
echo  [OK] Gateway C# siap

rem ---------------------------------------------------------------- jalankan
echo  [*] Menjalankan engine Python di 127.0.0.1:%ENGINE_PORT% ...
start "SMC Engine (Python) :%ENGINE_PORT%" /d "%ROOT%engine" cmd /k ""%VPY%" -m uvicorn app.main:app --host 127.0.0.1 --port %ENGINE_PORT%"

echo  [*] Menjalankan gateway C# di http://localhost:%WEB_PORT% ...
start "SMC Gateway (C#) :%WEB_PORT%" /d "%ROOT%gateway" cmd /k "dotnet run -c Release --no-build -- --urls http://localhost:%WEB_PORT% --Terminal:EngineUrl=http://127.0.0.1:%ENGINE_PORT%"

echo  [*] Menunggu server siap ...
set /a TRIES=0
:wait
set /a TRIES+=1
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://localhost:%WEB_PORT%/api/health' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 goto :ready
if %TRIES% geq 60 goto :ready
timeout /t 1 /nobreak >nul
goto :wait

:ready
start "" "http://localhost:%WEB_PORT%/"
echo.
echo  [OK] SMC Terminal berjalan:  http://localhost:%WEB_PORT%/
echo       Dua jendela log terbuka (Engine Python ^& Gateway C#). Jangan ditutup.
echo       Scan screener pertama butuh +-20 detik. Untuk berhenti: jalankan stop.bat
echo.
timeout /t 8 >nul
exit /b 0

:no_python
echo  [X] Python 3.10 atau lebih baru tidak ditemukan.
echo      Install dari https://www.python.org/downloads/ dan centang "Add python.exe to PATH".
goto :end_fail
:no_dotnet
echo  [X] .NET SDK 8 (atau lebih baru) tidak ditemukan.
echo      Install dari https://dotnet.microsoft.com/download/dotnet/8.0  (pilih "SDK x64").
goto :end_fail
:fail_venv
echo  [X] Gagal membuat virtualenv Python.
goto :end_fail
:fail_pip
echo  [X] Instalasi dependensi Python gagal. Periksa koneksi internet lalu jalankan ulang start.bat.
goto :end_fail
:fail_build
echo  [X] Build gateway C# gagal. Lihat pesan error di atas.
goto :end_fail
:end_fail
echo.
pause
exit /b 1
