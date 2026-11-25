@echo off
setlocal EnableExtensions
title SHARX Data Transfer
cd /d "%~dp0"

rem ==========================================================================
rem  run.bat             buka aplikasi desktop SHARX Data Transfer
rem  run.bat browser     mode browser / service (http://127.0.0.1:5080)
rem  run.bat simulator   jalankan simulator server + printer TIJ untuk uji coba
rem  run.bat publish     buat paket siap pakai di folder publish\ (butuh .NET 8 SDK)
rem  run.bat android     buat APK Android di publish\SharxDataTransfer.apk
rem                      (butuh .NET 8 SDK + "dotnet workload install android" + JDK 17)
rem
rem  Paket siap pakai: run.bat + folder app\ (program). Settings, counter dan
rem  log disimpan di folder data\ di samping run.bat.
rem ==========================================================================

set "MODE=%~1"
if "%MODE%"=="" set "MODE=desktop"
set "DATA=%~dp0data"

if /i "%MODE%"=="desktop" goto desktop
if /i "%MODE%"=="browser" goto browser
if /i "%MODE%"=="simulator" goto simulator
if /i "%MODE%"=="publish" goto publish
if /i "%MODE%"=="android" goto android
echo Mode tidak dikenal: %MODE%
echo Pakai: run.bat [desktop ^| browser ^| simulator ^| publish ^| android]
exit /b 1

rem --------------------------------------------------------------- desktop
:desktop
if exist "%~dp0app\SharxDataTransfer.exe" goto desktop_packaged
call :hassdk
if errorlevel 1 goto desktop_fallback
echo Menyiapkan aplikasi desktop...
dotnet build "src\Sharx.Desktop\Sharx.Desktop.csproj" -c Release -nologo -v quiet
if errorlevel 1 goto failed
start "" "%~dp0src\Sharx.Desktop\bin\Release\net8.0-windows\SharxDataTransfer.exe" --data "%DATA%"
exit /b 0

:desktop_packaged
start "" "%~dp0app\SharxDataTransfer.exe" --data "%DATA%"
exit /b 0

:desktop_fallback
if not exist "%~dp0publish\app\SharxDataTransfer.exe" goto nosdk
start "" "%~dp0publish\app\SharxDataTransfer.exe" --data "%DATA%"
exit /b 0

rem --------------------------------------------------------------- browser
:browser
if exist "%~dp0app\SharxDataTransfer.Server.exe" goto browser_packaged
call :hassdk
if errorlevel 1 goto nosdk
dotnet run --project "src\Sharx.Host\Sharx.Host.csproj" -c Release -- --data "%DATA%"
exit /b %errorlevel%

:browser_packaged
"%~dp0app\SharxDataTransfer.Server.exe" --data "%DATA%"
exit /b %errorlevel%

rem --------------------------------------------------------------- simulator
:simulator
set "SIM=%~dp0app\SharxSimulator.exe"
if exist "%SIM%" goto simulator_start
call :hassdk
if errorlevel 1 goto nosdk
dotnet build "tools\Sharx.Simulator\Sharx.Simulator.csproj" -c Release -nologo -v quiet
if errorlevel 1 goto failed
set "SIM=%~dp0tools\Sharx.Simulator\bin\Release\net8.0\SharxSimulator.exe"

:simulator_start
start "Simulator Printer TIJ (port 9100)" "%SIM%" printer --port 9100 --messages 867892,555111 --print-interval 2
start "Simulator MQTT Broker (port 1883)" "%SIM%" broker --port 1883
echo Simulator berjalan di 2 jendela baru.
echo Di Settings - Koneksi isi: MQTT broker 127.0.0.1 port 1883 (topic sharx/job, feedback sharx/printed),
echo Printer 127.0.0.1 port 9100, simpan, lalu tekan Start Transfer.
exit /b 0

rem --------------------------------------------------------------- publish
:publish
call :hassdk
if errorlevel 1 goto nosdk
rem Ketiga program berbagi satu runtime .NET di publish\app (lebih kecil).
set "PUB=-c Release -r win-x64 --self-contained true -p:DebugType=none -nologo"
dotnet test "tests\Sharx.Core.Tests\Sharx.Core.Tests.csproj" -c Release -nologo
if errorlevel 1 goto failed
dotnet publish "src\Sharx.Desktop\Sharx.Desktop.csproj" %PUB% -o "publish\app"
if errorlevel 1 goto failed
dotnet publish "src\Sharx.Host\Sharx.Host.csproj" %PUB% -o "publish\app"
if errorlevel 1 goto failed
dotnet publish "tools\Sharx.Simulator\Sharx.Simulator.csproj" %PUB% -o "publish\app"
if errorlevel 1 goto failed
del /q "publish\app\*.xml" 2>nul
copy /y "run.bat" "publish\run.bat" >nul
copy /y "README.md" "publish\README.md" >nul
echo.
echo Selesai. Salin folder publish\ ke PC produksi lalu jalankan publish\run.bat
echo (tidak perlu .NET SDK di PC produksi; butuh WebView2 Runtime untuk aplikasi desktop).
exit /b 0

rem --------------------------------------------------------------- android
:android
call :hassdk
if errorlevel 1 goto nosdk
echo Membuat APK Android (fitur dan UI sama dengan aplikasi desktop)...
rem Android SDK dipasang otomatis jika belum ada.
dotnet build "src\Sharx.Android\Sharx.Android.csproj" -t:InstallAndroidDependencies -f net8.0-android -p:AcceptAndroidSdkLicenses=True -nologo -v quiet
if errorlevel 1 goto failed
dotnet publish "src\Sharx.Android\Sharx.Android.csproj" -c Release -f net8.0-android -nologo -o "publish\android"
if errorlevel 1 goto failed
copy /y "publish\android\com.wijayacode.sharxdatatransfer-Signed.apk" "publish\SharxDataTransfer.apk" >nul
echo.
echo Selesai: publish\SharxDataTransfer.apk
echo Salin ke tablet / HP Android 8.0+ lalu pasang (izinkan instal dari sumber tidak dikenal).
exit /b 0

rem --------------------------------------------------------------- helper
:hassdk
where dotnet >nul 2>nul
if errorlevel 1 exit /b 1
exit /b 0

:nosdk
echo .NET 8 SDK belum terpasang di komputer ini.
echo Download: https://dotnet.microsoft.com/download/dotnet/8.0
echo Atau jalankan "run.bat publish" di komputer lain lalu salin folder publish\ ke sini.
pause
exit /b 1

:failed
echo.
echo Gagal. Periksa pesan error di atas.
pause
exit /b 1
