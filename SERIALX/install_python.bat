@echo off
rem ============================================================
rem  SERIALX - Installer Python otomatis
rem  Jalankan file ini DULU jika muncul error:
rem  "'python' is not recognized as an internal or external command"
rem ============================================================
cd /d "%~dp0"
title SERIALX - Install Python

echo [SERIALX] Mengecek apakah Python sudah terinstall...
where py >nul 2>nul && ( echo [SERIALX] Python launcher ^(py^) ditemukan. Tidak perlu install ulang. & goto :checkver )
where python >nul 2>nul && ( echo [SERIALX] python.exe ditemukan di PATH. & goto :checkver )

echo [SERIALX] Python TIDAK ditemukan. Mengunduh Python 3.12 (64-bit) dari python.org ...
set "URL=https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
set "OUT=%TEMP%\python-installer.exe"

powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%URL%' -OutFile '%OUT%'"
if not exist "%OUT%" (
    echo.
    echo [SERIALX] Gagal mengunduh otomatis ^(kemungkinan tidak ada internet/firewall^).
    echo           Download manual di: https://www.python.org/downloads/
    echo           Saat install, WAJIB centang "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [SERIALX] Menginstal Python diam-diam ^(silent^) + Add to PATH ...
"%OUT%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
if errorlevel 1 (
    echo [SERIALX] Installer gagal. Coba jalankan ulang sebagai Administrator.
    pause
    exit /b 1
)

rem --- segarkan PATH di jendela ini supaya langsung bisa dipakai ---
set "PATH="
for /f "skip=2 tokens=1,2,*" %%a in ('reg query HKCU\Environment /v Path 2^>nul') do set "PATH=%%c;%SystemRoot%\System32"
for /f "skip=2 tokens=1,2,*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "PATH=%PATH%;%%c"

:checkver
echo.
echo [SERIALX] Versi Python yang aktif:
py -3 --version 2>nul || python --version 2>nul
echo.
echo [SERIALX] Selesai. Sekarang double-click run.bat untuk menjalankan SERIALX.
pause
exit /b 0
