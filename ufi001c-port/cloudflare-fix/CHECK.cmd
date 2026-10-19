@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where ssh.exe >nul 2>nul
if errorlevel 1 goto no_ssh
set "UFI_CF_RUN=%RANDOM%-%RANDOM%-%RANDOM%"
set "UFI_CF_DIR=%USERPROFILE%\Downloads\UFI-CLOUDFLARE-%UFI_CF_RUN%"
mkdir "%UFI_CF_DIR%"
if errorlevel 1 exit /b 1
echo Проверка Cloudflare и FakeIP. Подключитесь к Wi-Fi модема UFI001C.
echo Введите пароль root модема, когда его запросит SSH.
ssh.exe -T -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 root@192.168.1.1 "sh -s 2>&1" <"%~dp0check.sh" >"%UFI_CF_DIR%\UFI-CLOUDFLARE.txt"
set "UFI_CF_EXIT=%ERRORLEVEL%"
type "%UFI_CF_DIR%\UFI-CLOUDFLARE.txt"
>>"%UFI_CF_DIR%\UFI-CLOUDFLARE.txt" echo SSH_EXIT=%UFI_CF_EXIT%
explorer.exe "%UFI_CF_DIR%"
pause
exit /b %UFI_CF_EXIT%
:no_ssh
echo Не найден встроенный OpenSSH Client Windows.
pause
exit /b 1
