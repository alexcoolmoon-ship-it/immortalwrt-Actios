@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where ssh.exe >nul 2>nul
if errorlevel 1 goto no_ssh
set "UFI_DNS_RUN=%RANDOM%-%RANDOM%-%RANDOM%"
set "UFI_DNS_DIR=%USERPROFILE%\Downloads\UFI-DNS-%UFI_DNS_RUN%"
mkdir "%UFI_DNS_DIR%"
if errorlevel 1 exit /b 1
echo Подключитесь к Wi-Fi модема UFI001C. Введите пароль root, когда его запросит SSH.
echo Проверка только читает состояние и отправляет тестовые запросы. Настройки не меняются.
ssh.exe -T -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 root@192.168.1.1 "sh -s 2>&1" <"%~dp0check.sh" >"%UFI_DNS_DIR%\UFI-DNS.txt"
set "UFI_DNS_EXIT=%ERRORLEVEL%"
>>"%UFI_DNS_DIR%\UFI-DNS.txt" echo SSH_EXIT=%UFI_DNS_EXIT%
type "%UFI_DNS_DIR%\UFI-DNS.txt"
explorer.exe "%UFI_DNS_DIR%"
pause
exit /b %UFI_DNS_EXIT%
:no_ssh
echo Не найден встроенный OpenSSH Client Windows.
pause
exit /b 1
