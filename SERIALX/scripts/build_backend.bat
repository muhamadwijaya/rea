@echo off
rem Build backend C# .NET 8 menjadi bin\serialx-server.exe (opsional - run.bat otomatis mencoba)
cd /d "%~dp0.."
where dotnet >nul 2>nul || (echo [.NET SDK tidak ditemukan. Install dari https://dotnet.microsoft.com & pause & exit /b 1)
dotnet publish backend\SerialX.Server -c Release -r win-x64 --self-contained -o bin
if errorlevel 1 ( echo Build gagal. & pause & exit /b 1 )
echo OK: bin\serialx-server.exe siap. UI akan memakai mode "csharp-native".
pause
