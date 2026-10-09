#!/usr/bin/env python3
"""Package the reviewed setup/UI fixes for an existing UFI001C v3.1.1.

Uses fixed hashes of the base commit; never rewrites firmware image checksums.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def build(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    staging = destination / 'package'
    staging.mkdir()
    baseline = json.loads((HERE / 'baseline-3.1.1.json').read_text())
    records = []
    for target, spec in baseline['files'].items():
        source = REPO / spec['source']
        output = staging / 'payload' / target
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        records.append(' '.join([spec['old_sha256'] or 'NEW', spec['alternate_old_sha256'] or '-',
                                 digest, spec['mode'], target]))
    (staging / 'FILES').write_text('\n'.join(records) + '\n')
    shutil.copyfile(HERE / 'install.sh', staging / 'install.sh')
    sums = []
    for path in sorted(staging.rglob('*')):
        if path.is_file():
            sums.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(staging)))
    (staging / 'SHA256SUMS').write_text('\n'.join(sums) + '\n')
    archive = destination / 'ufi-setup312.tar.gz'
    with tarfile.open(archive, 'w:gz') as out:
        for path in sorted(staging.rglob('*')):
            if path.is_file():
                out.add(path, arcname=str(path.relative_to(staging)), recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    apply = f'''@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
echo Закройте вкладку настроек Podkop в браузере перед установкой.
echo Подключение к модему 192.168.1.1. При запросе введите пароль root.
ssh root@192.168.1.1 "mkdir -p /tmp/ufi-setup312"
if errorlevel 1 goto failed
scp -O "ufi-setup312.tar.gz" root@192.168.1.1:/tmp/ufi-setup312.tar.gz
if errorlevel 1 goto failed
ssh root@192.168.1.1 "echo {digest} /tmp/ufi-setup312.tar.gz | sha256sum -c && tar -xzf /tmp/ufi-setup312.tar.gz -C /tmp/ufi-setup312 && sh /tmp/ufi-setup312/install.sh"
if errorlevel 1 goto failed
echo.
echo Исправление установлено. Откройте Podkop, нажмите Ctrl+F5.
echo Если ссылка уже сохранена, повторно вставлять её не нужно.
echo Если секции пустые: вставьте свою ссылку, сохраните и примените.
echo Затем запустите START-CHECK.cmd.
start "" "http://192.168.1.1/cgi-bin/luci/admin/services/podkop"
popd
pause
exit /b 0
:failed
echo.
echo Установка остановлена. Сохраните текст ошибки из этого окна.
popd
pause
exit /b 1
'''
    check = '''@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
echo Запуск Podkop. Настройки со ссылкой должны быть сохранены в LuCI.
ssh root@192.168.1.1 "/etc/init.d/podkop restart"
if errorlevel 1 goto failed
echo Ожидание запуска 15 секунд...
timeout /t 15 /nobreak >nul
ssh root@192.168.1.1 "cat /etc/ufi001c-setup-fix 2>/dev/null; sh /usr/lib/podkop/ufi_dns_tunnel.sh check && /etc/init.d/podkop enable" > "ufi-start-result.txt" 2>&1
set "UFI_CHECK_EXIT=%ERRORLEVEL%"
type "ufi-start-result.txt"
if not "%UFI_CHECK_EXIT%"=="0" goto diagnostics
curl.exe -4 --noproxy "*" -I --connect-timeout 10 --max-time 20 https://www.google.com/ >> "ufi-start-result.txt" 2>&1
set "UFI_HTTP_EXIT=%ERRORLEVEL%"
type "ufi-start-result.txt"
if not "%UFI_HTTP_EXIT%"=="0" goto diagnostics
echo DNS через прокси работает. Автозапуск включён. HTTP-проверка выполнена.
popd
pause
exit /b 0
:diagnostics
echo.
echo Проверка не пройдена. Журнал сохраняется в ufi-start-result.txt рядом со скриптом.
ssh root@192.168.1.1 "logread | grep -E 'podkop|sing-box' | tail -90" >> "ufi-start-result.txt" 2>&1
popd
pause
exit /b 1
:failed
echo Ошибка SSH. Сохраните сообщение из окна.
popd
pause
exit /b 1
'''
    for name, text in [('APPLY.cmd', apply), ('START-CHECK.cmd', check)]:
        (destination / name).write_bytes(text.replace('\n', '\r\n').encode('utf-8'))
    shutil.rmtree(staging)
    return {'archive': str(archive), 'sha256': digest, 'baseline': baseline['commit']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination')
    args = parser.parse_args()
    print(json.dumps(build(args.destination), indent=2))
