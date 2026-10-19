@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where ssh.exe >nul 2>nul
if errorlevel 1 goto no_ssh
set "UFI_DNS_RUN=%RANDOM%-%RANDOM%-%RANDOM%"
set "UFI_DNS_DIR=%USERPROFILE%\Downloads\UFI-CHECK-DNS-%UFI_DNS_RUN%"
mkdir "%UFI_DNS_DIR%"
if errorlevel 1 exit /b 1
echo Проверка DNS модема: Яндекс, Cloudflare через прокси, FakeIP.
echo Подключитесь к Wi-Fi модема UFI001C.
echo Введите пароль root модема, когда его запросит SSH.
ssh.exe -T -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 root@192.168.1.1 "sh -s 2>&1" <"%~dp0check-dns.sh" >"%UFI_DNS_DIR%\UFI-CHECK-DNS.txt"
set "UFI_DNS_EXIT=%ERRORLEVEL%"
type "%UFI_DNS_DIR%\UFI-CHECK-DNS.txt"
>>"%UFI_DNS_DIR%\UFI-CHECK-DNS.txt" echo SSH_EXIT=%UFI_DNS_EXIT%
explorer.exe "%UFI_DNS_DIR%"
pause
exit /b %UFI_DNS_EXIT%
:no_ssh
echo Не найден встроенный OpenSSH Client Windows.
pause
exit /b 1
