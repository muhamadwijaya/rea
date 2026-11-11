@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "%~dp0app\SharxInvest.exe" (
  echo  [!] Aplikasi belum dibuild. Menjalankan setup dulu ...
  call "%~dp0setup.bat" --quiet || exit /b 1
)
powershell -NoProfile -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\SHARX INVEST.lnk'); $s.TargetPath = '%~dp0app\SharxInvest.exe'; $s.WorkingDirectory = '%~dp0app'; $s.IconLocation = '%~dp0app\SharxInvest.exe,0'; $s.Description = 'SHARX INVEST - Saham, Forex, Kripto'; $s.Save()"
if errorlevel 1 (
  echo  [X] Gagal membuat shortcut.
) else (
  echo  [OK] Shortcut "SHARX INVEST" dibuat di Desktop.
)
timeout /t 3 >nul
