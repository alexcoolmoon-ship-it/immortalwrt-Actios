@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo Подключение к ранее запущенному применению. Новый процесс не создаётся.
ssh.exe -o ServerAliveInterval=10 -o ServerAliveCountMax=3 root@192.168.1.1 "sh /root/ufi-profile-fix/ufi-apply-profile.sh wait"
if "%ERRORLEVEL%"=="3" goto rebooted
if errorlevel 1 goto failed
call "%~dp0GET-REPORT.cmd" nopause
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo Подключение прервано. После возврата Wi-Fi/USB снова запустите CONTINUE.cmd.
pause
exit /b 1
:rebooted
echo Модем перезагрузился: прежний процесс уже не возобновить.
echo Для новой проверки используйте CHECK-LTE.cmd из USB-комплекта v3.2.0.
pause
exit /b 3
