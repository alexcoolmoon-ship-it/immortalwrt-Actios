@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
ssh.exe root@192.168.1.1 "sh /root/ufi-profile-fix/ufi-apply-profile.sh export"
if errorlevel 1 goto failed
set "UFI_REPORT_DIR=%USERPROFILE%\Downloads\UFI-LTE-Fix-%RANDOM%"
mkdir "%UFI_REPORT_DIR%"
if errorlevel 1 goto failed
scp.exe -O root@192.168.1.1:/root/ufi-profile/ufi-profile-report.tar.gz "%UFI_REPORT_DIR%\ufi-profile-report.tar.gz"
if errorlevel 1 goto failed
echo Отчёт сохранён. Пришлите ufi-profile-report.tar.gz.
explorer.exe "%UFI_REPORT_DIR%"
if /i not "%~1"=="nopause" pause
exit /b 0
:failed
echo Отчёт не получен. Сохраните текст ошибки.
if /i not "%~1"=="nopause" pause
exit /b 1
