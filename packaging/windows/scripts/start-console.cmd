@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "INSTALL_DIR=%SCRIPT_DIR%.."
set "CONFIG_PATH=%APPDATA%\LMIT-2\wiki-only.toml"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start-console.ps1" -InstallDir "%INSTALL_DIR%" -ConfigPath "%CONFIG_PATH%"
