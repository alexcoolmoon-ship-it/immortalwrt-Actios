@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not defined UFI_EDL_ROOT set "UFI_EDL_ROOT=%USERPROFILE%\edl"
if not exist "%UFI_EDL_ROOT%\.venv\Scripts\python.exe" goto missing
set "PYTHONUTF8=1"
"%UFI_EDL_ROOT%\.venv\Scripts\python.exe" -u "%~dp0stock_install.py" bundle
set "UFI_RESULT=%ERRORLEVEL%"
echo.
if not "%UFI_RESULT%"=="0" echo Выполнение остановлено. Сохраните папку UFI001C-backups и журнал.
pause
exit /b %UFI_RESULT%
:missing
echo Не найден рабочий EDL: %UFI_EDL_ROOT%
echo На этом ПК нужен тот же Python/EDL и USB-драйвер, с которыми уже прошивался модем.
echo Если EDL находится в другой папке, задайте UFI_EDL_ROOT и повторите запуск.
echo Инструкция: README-STOCK-RU.md
pause
exit /b 1
