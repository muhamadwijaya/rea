@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "%~dp0app\SMCTerminal.exe" (
  echo  [!] Aplikasi belum dibuild. Menjalankan setup dulu ...
  call "%~dp0setup.bat" --quiet || exit /b 1
)
powershell -NoProfile -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\SMC Terminal.lnk'); $s.TargetPath = '%~dp0app\SMCTerminal.exe'; $s.WorkingDirectory = '%~dp0app'; $s.Description = 'SMC Terminal - Saham, Forex, Kripto'; $s.Save()"
if errorlevel 1 (
  echo  [X] Gagal membuat shortcut.
) else (
  echo  [OK] Shortcut "SMC Terminal" dibuat di Desktop.
)
timeout /t 3 >nul
