@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "ROOT=%~dp0"
set "QUIET="
set "REBUILD="
for %%A in (%*) do (
  if /i "%%~A"=="--quiet" set "QUIET=1"
  if /i "%%~A"=="--rebuild" set "REBUILD=1"
)

if not defined QUIET (
  echo.
  echo  ==========================================================
  echo    SMC TERMINAL - Setup  ^(Python engine + aplikasi desktop C#^)
  echo  ==========================================================
  echo.
)

rem ---------------------------------------------------------------- Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :no_python

rem ---------------------------------------------------------------- .NET
where dotnet >nul 2>nul || goto :no_dotnet
dotnet --list-sdks 2>nul | findstr /r /c:"^[89]\." /c:"^1[0-9]\." >nul || goto :no_dotnet

rem ---------------------------------------------------------------- venv + dependensi Python
if not exist "%ROOT%engine\.venv\Scripts\python.exe" (
  echo  [*] Membuat virtualenv Python ^(sekali saja^)...
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

rem ---------------------------------------------------------------- build aplikasi desktop (C#)
if defined REBUILD del /q "%ROOT%app\VERSION" >nul 2>nul
fc /b "%ROOT%VERSION" "%ROOT%app\VERSION" >nul 2>nul
if not errorlevel 1 goto :built
tasklist /FI "IMAGENAME eq SMCTerminal.exe" 2>nul | find /i "SMCTerminal.exe" >nul
if not errorlevel 1 (
  echo  [!] SMC Terminal sedang berjalan - tutup dulu aplikasinya lalu jalankan setup lagi.
  goto :end_fail
)
echo  [*] Build aplikasi desktop C# ^(1-3 menit pada run pertama^)...
dotnet publish "%ROOT%desktop\SmcTerminal.Desktop.csproj" -c Release -r win-x64 --self-contained false -o "%ROOT%app" -nologo -v q || goto :fail_build
copy /y "%ROOT%VERSION" "%ROOT%app\VERSION" >nul
echo  [OK] Aplikasi siap: %ROOT%app\SMCTerminal.exe
:built

if not defined QUIET (
  echo.
  echo  [OK] Setup selesai. Jalankan start.bat ^(atau buat-shortcut.bat untuk ikon di Desktop^).
  echo.
  pause
)
exit /b 0

:no_python
echo  [X] Python 3.10 atau lebih baru tidak ditemukan.
echo      Install dari https://www.python.org/downloads/ dan centang "Add python.exe to PATH".
goto :end_fail
:no_dotnet
echo  [X] .NET SDK 8 ^(atau lebih baru^) tidak ditemukan.
echo      Install dari https://dotnet.microsoft.com/download/dotnet/8.0  ^(pilih "SDK x64"^).
goto :end_fail
:fail_venv
echo  [X] Gagal membuat virtualenv Python.
goto :end_fail
:fail_pip
echo  [X] Instalasi dependensi Python gagal. Periksa koneksi internet lalu jalankan ulang.
goto :end_fail
:fail_build
echo  [X] Build aplikasi C# gagal. Lihat pesan error di atas.
goto :end_fail
:end_fail
echo.
pause
exit /b 1
