#!/usr/bin/env python3
"""Package a source-hash-bound live patch, never modify firmware images."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

HERE=Path(__file__).resolve().parent
PORT=HERE.parent

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def build(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    staging=output/'staging';staging.mkdir()
    manifest=json.loads((HERE/'baseline-3.1.2.json').read_text())
    rows=[]
    for target, spec in manifest['files'].items():
        source=PORT/spec['source']; dest=staging/'payload'/target
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
        rows.append(' '.join([spec['old_sha256'],spec.get('minified_sha256','-'),sha(dest),spec['mode'],target]))
    (staging/'FILES').write_text('\n'.join(rows)+'\n')
    for name in ('install.sh','restore.sh'):shutil.copyfile(HERE/name,staging/name)
    sums=[sha(p)+'  '+p.relative_to(staging).as_posix() for p in sorted(staging.rglob('*')) if p.is_file()]
    (staging/'SHA256SUMS').write_text('\n'.join(sums)+'\n')
    archive=output/'ufi-stability.tar.gz'
    with tarfile.open(archive,'w:gz') as out:
        for p in sorted(staging.rglob('*')):
            if p.is_file():out.add(p,arcname=p.relative_to(staging).as_posix(),recursive=False)
    checksum=sha(archive)
    apply=f'''@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Подключите первый модем с OpenWrt 3.1.2. Закройте вкладку Podkop.
ssh root@192.168.1.1 "mkdir -p /tmp/ufi-stability"
if errorlevel 1 goto failed
scp -O "ufi-stability.tar.gz" root@192.168.1.1:/tmp/ufi-stability.tar.gz
if errorlevel 1 goto failed
ssh root@192.168.1.1 "echo {checksum} /tmp/ufi-stability.tar.gz | sha256sum -c && tar -xzf /tmp/ufi-stability.tar.gz -C /tmp/ufi-stability && sh /tmp/ufi-stability/install.sh" > APPLY-result.txt 2>&1
set "UFI_RC=%ERRORLEVEL%"
type APPLY-result.txt
if not "%UFI_RC%"=="0" goto failed
echo Затем запустите START-CHECK.cmd и WATCH.cmd.
pause
exit /b 0
:failed
echo Установка остановлена. Пришлите APPLY-result.txt или текст ошибки SSH.
pause
exit /b 1
'''
    check='''@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Ссылки прокси должны быть сохранены. Перезапускается только Podkop.
ssh root@192.168.1.1 "/etc/init.d/podkop restart" > ufi-start-result.txt 2>&1
if errorlevel 1 goto failed
timeout /t 12 /nobreak >nul
ssh root@192.168.1.1 "sh /usr/lib/podkop/ufi_dns_tunnel.sh check" >> ufi-start-result.txt 2>&1
set "UFI_RC=%ERRORLEVEL%"
type ufi-start-result.txt
if not "%UFI_RC%"=="0" goto failed
echo Локальная маршрутизация и DNS через прокси прошли проверку.
pause
exit /b 0
:failed
echo Проверка не пройдена. Пришлите ufi-start-result.txt.
pause
exit /b 1
'''
    watch='''@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Пять минут: откройте панель модема и повторите действия, вызывавшие обрыв.
echo Опрос сохраняет boot_id и число пересозданий USB, восстанавливает SSH после обрыва.
echo UFI monitoring > ufi-watch.txt
for /L %%i in (1,1,21) do (
 echo ==== SNAPSHOT %%i ==== >> ufi-watch.txt
 ssh -o ConnectTimeout=4 -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=1 root@192.168.1.1 "sh /usr/sbin/ufi-stability-report" >> ufi-watch.txt 2>&1
 if not %%i==21 timeout /t 15 /nobreak >nul
)
echo Готово. Пришлите ufi-watch.txt.
pause
'''
    rollback='''@echo off
setlocal
chcp 65001 >nul
echo Возврат файлов и настроек USB, сохранённых перед исправлением.
ssh root@192.168.1.1 "b=$(cat /root/ufi-stability-last-backup) && sh $b/restore.sh"
pause
'''
    for name,content in [('APPLY.cmd',apply),('START-CHECK.cmd',check),('WATCH.cmd',watch),('ROLLBACK.cmd',rollback)]:
        (output/name).write_bytes(content.replace('\n','\r\n').encode('utf-8'))
    shutil.rmtree(staging)
    return {'file':str(archive),'sha256':checksum,'base_commit':manifest['commit']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output');args=parser.parse_args()
    print(json.dumps(build(args.output),indent=2))
