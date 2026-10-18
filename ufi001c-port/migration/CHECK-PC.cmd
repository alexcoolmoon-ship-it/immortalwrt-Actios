@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
"%~dp0runtime\UFI001C-Installer.exe" --self-test
set "UFI_RESULT=%ERRORLEVEL%"
if "%UFI_RESULT%"=="0" echo Программы и USB-библиотека готовы. Подключать модем для этой проверки не нужно.
pause
exit /b %UFI_RESULT%
