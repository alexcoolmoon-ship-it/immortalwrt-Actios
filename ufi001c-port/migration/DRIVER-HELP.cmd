@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
"%~dp0runtime\UFI001C-Installer.exe" driver-help
set "UFI_RESULT=%ERRORLEVEL%"
pause
exit /b %UFI_RESULT%
