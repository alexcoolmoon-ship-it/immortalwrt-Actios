@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call "%~dp0RUN.cmd" check-backup
set "UFI_RESULT=%ERRORLEVEL%"
if not "%UFI_RESULT%"=="0" echo Выполнение остановлено. Сохраните текст ошибки и папку UFI001C-backups.
pause
exit /b %UFI_RESULT%
