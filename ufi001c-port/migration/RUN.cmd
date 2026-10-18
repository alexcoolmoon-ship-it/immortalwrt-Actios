@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if exist "%~dp0runtime\UFI001C-Installer.exe" goto portable
if not defined UFI_EDL_ROOT set "UFI_EDL_ROOT=%USERPROFILE%\edl"
if not exist "%UFI_EDL_ROOT%\.venv\Scripts\python.exe" goto missing
"%UFI_EDL_ROOT%\.venv\Scripts\python.exe" -u "%~dp0stock_install.py" %1
exit /b %ERRORLEVEL%
:portable
"%~dp0runtime\UFI001C-Installer.exe" %1
exit /b %ERRORLEVEL%
:missing
echo Не найден переносимый установщик. Распакуйте весь USB-ZIP из Releases.
echo Исходники не содержат готовой прошивки и Windows-программы.
exit /b 1
