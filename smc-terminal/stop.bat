@echo off
setlocal EnableExtensions
set "ENGINE_PORT=8765"
set "WEB_PORT=5080"
echo  [*] Menghentikan SMC Terminal ...
taskkill /FI "WINDOWTITLE eq SMC Engine*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq SMC Gateway*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Administrator:  SMC Engine*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Administrator:  SMC Gateway*" /T /F >nul 2>nul
rem Fallback: proses yang masih mendengarkan di port aplikasi.
for %%P in (%ENGINE_PORT% %WEB_PORT%) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:":%%P .*LISTENING"') do taskkill /PID %%I /T /F >nul 2>nul
)
echo  [OK] Selesai.
timeout /t 2 >nul
