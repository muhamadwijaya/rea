@echo off
echo  [*] Menghentikan SHARX INVEST ...
rem Engine Python berada dalam job object milik aplikasi/gateway, jadi ikut berhenti bersama prosesnya.
taskkill /IM SharxInvest.exe /T /F >nul 2>nul
taskkill /IM SMCTerminal.exe /T /F >nul 2>nul
taskkill /IM SmcTerminal.Gateway.exe /T /F >nul 2>nul
echo  [OK] Selesai.
timeout /t 2 >nul
