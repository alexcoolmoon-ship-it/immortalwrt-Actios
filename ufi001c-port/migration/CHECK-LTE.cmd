@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "UFI_RUN=%RANDOM%-%RANDOM%-%RANDOM%"
set "UFI_DEST=%USERPROFILE%\Downloads\UFI-Postboot-%UFI_RUN%"
echo Проверка текущего состояния UFI001C после перезагрузки.
echo Введите пароль root, если Windows его запросит.
echo Обычно 30-90 секунд. Отчёт будет новым при каждом запуске.
ssh.exe root@192.168.1.1 "umask 077 && mkdir -p /root/ufi-postboot-check && test ! -L /root/ufi-postboot-check"
if errorlevel 1 goto failed
scp.exe -O "%~dp0ufi-postboot-check.sh" root@192.168.1.1:/root/ufi-postboot-check/check-%UFI_RUN%.upload
if errorlevel 1 goto failed
ssh.exe -o ServerAliveInterval=10 -o ServerAliveCountMax=3 root@192.168.1.1 "sh -n /root/ufi-postboot-check/check-%UFI_RUN%.upload && sh /root/ufi-postboot-check/check-%UFI_RUN%.upload collect %UFI_RUN%"
if errorlevel 1 goto failed
mkdir "%UFI_DEST%"
if errorlevel 1 goto failed
scp.exe -O root@192.168.1.1:/root/ufi-postboot-check/ufi-postboot-report-%UFI_RUN%.tar.gz "%UFI_DEST%\ufi-postboot-report.tar.gz"
if errorlevel 1 goto failed
echo Готово. Пришлите ufi-postboot-report.tar.gz из открывшейся папки.
explorer.exe "%UFI_DEST%"
pause
exit /b 0
:failed
echo Проверка или получение отчёта не завершены. Сохраните текст ошибки.
echo Если модем перезагрузился, подключитесь к нему и снова запустите CHECK.cmd.
echo Каждый CHECK запускает новую проверку, частоты и настройки не меняются.
pause
exit /b 1
