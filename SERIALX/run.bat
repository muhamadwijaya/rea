@echo off
rem ============================================================
rem  SERIALX - Industrial Serialization & Marking Suite
rem  Double-click file ini untuk menjalankan aplikasi di Windows
rem ============================================================
cd /d "%~dp0"
title SERIALX

rem --- cari Python ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo [SERIALX] Python TIDAK ditemukan. Install Python 3.11+ dari https://python.org
    echo           (jangan lupa centang "Add python.exe to PATH" saat install).
    pause
    exit /b 1
)

rem --- buat virtualenv sekali saja ---
if not exist ".venv\Scripts\python.exe" (
    echo [SERIALX] Membuat virtual environment .venv ...
    %PY% -m venv .venv
    if errorlevel 1 ( echo [SERIALX] Gagal membuat venv. & pause & exit /b 1 )
)
set "VPY=.venv\Scripts\python.exe"

rem --- install dependensi bila belum ada ---
"%VPY%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [SERIALX] Menginstall dependensi ^(pyside6, openpyxl^)... tunggu sebentar.
    "%VPY%" -m pip install --upgrade pip >nul 2>nul
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 ( echo [SERIALX] Gagal install dependensi. Periksa koneksi internet. & pause & exit /b 1 )
)

rem --- build backend C# sekali bila .NET SDK tersedia ---
if not exist "bin\serialx-server.exe" (
    where dotnet >nul 2>nul
    if not errorlevel 1 (
        echo [SERIALX] Membangun backend C# .NET ^(biar kencang^)...
        dotnet publish backend\SerialX.Server -c Release -r win-x64 --self-contained -o bin >nul 2>nul
        if errorlevel 1 echo [SERIALX] Catatan: build C# gagal - aplikasi akan pakai engine Python fallback.
    ) else (
        echo [SERIALX] .NET SDK tidak ditemukan - memakai engine Python fallback ^(tetap berfungsi^).
    )
)

echo [SERIALX] Menjalankan aplikasi...
"%VPY%" main.py %*
if errorlevel 1 (
    echo.
    echo [SERIALX] Aplikasi berhenti dengan error ^(kode %errorlevel%^). Log: data\logs\serialx.log
    pause
)
