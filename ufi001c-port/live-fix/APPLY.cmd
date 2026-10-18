@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo Применение LTE: исключаем B7 и B20, остальные диапазоны сохраняем.
echo LTE прервётся. Обычно 4-7 минут. Не отключайте питание.
ssh.exe root@192.168.1.1 "test ! -d /tmp/ufi-profile.lock && test ! -d /tmp/ufi-diagnose.lock && test ! -d /tmp/ufi-finish.lock && umask 077 && mkdir -p /root/ufi-profile-fix"
if errorlevel 1 goto failed
scp.exe -O "%~dp0ufi-apply-profile.sh" root@192.168.1.1:/root/ufi-profile-fix/apply.upload
if errorlevel 1 goto failed
ssh.exe root@192.168.1.1 "sh -n /root/ufi-profile-fix/apply.upload && mv /root/ufi-profile-fix/apply.upload /root/ufi-profile-fix/ufi-apply-profile.sh"
if errorlevel 1 goto failed
ssh.exe -o ServerAliveInterval=10 -o ServerAliveCountMax=3 root@192.168.1.1 "sh /root/ufi-profile-fix/ufi-apply-profile.sh new-wait"
if errorlevel 1 goto failed
call "%~dp0GET-REPORT.cmd" nopause
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo Сохраните текст ошибки. Если применение уже началось, повторно APPLY не запускайте.
echo Подключитесь к модему и запустите CONTINUE.cmd.
pause
exit /b 1
