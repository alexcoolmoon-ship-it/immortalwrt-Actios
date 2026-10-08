#!/usr/bin/env python3
"""UFI001C stock conversion, using a separately saved full backup and EDL.

This is a narrowly scoped first-stock-install candidate, not a generic MSM8916
flasher. The Windows/device integration still needs an actual stock-unit test.
All image/GPT/profile checks precede writes. No downloaded code is executed.
"""
import argparse
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
import zlib

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location('ufi_update', HERE / 'ufi-install.py')
update = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update)
from radio_backup import read_at, read_gpt, copy_and_hash

VERSION = '3.0-rc1'
SECTOR = 512
GIB = 1024**3
NV = ('modemst1', 'modemst2', 'fsg', 'fsc', 'sec')
# These two original areas are left untouched throughout conversion. They bind
# recovery to the physical unit, including after a partial GPT/bootloader write.
ANCHORS = ('modemst1', 'modemst2')
LIMITS = {'boot.img': 64*1024**2, 'system.img': GIB,
          'SHA256SUMS': 262144, 'ACCESS.txt': 16384, 'build.config': 2*1024**2,
          'xhttp-validation.json': 65536, 'addons.lock.json': 65536}


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)


def load_profile(base):
    return json.loads((Path(base) / 'stock-profile.json').read_text(encoding='utf-8'))


def validate_gpt(dump, disk_bytes=None):
    """Check BOTH GPT copies, CRCs, locations, names and the disk's exact end."""
    if hasattr(dump, 'read'):
        size = disk_bytes
        stream = contextlib.nullcontext(dump)
    else:
        dump = Path(dump)
        size = dump.stat().st_size
        stream = dump.open('rb')
    with stream as f:
        parts = read_gpt(f, size, SECTOR)
        if len({p['name'] for p in parts}) != len(parts):
            raise ValueError('Duplicate GPT partition names')
        primary = read_at(f, SECTOR, SECTOR)
        last = size // SECTOR - 1
        if struct.unpack_from('<Q', primary, 32)[0] != last:
            raise ValueError('GPT backup is not at the physical disk end')
        backup = read_at(f, last*SECTOR, SECTOR)
        if backup[:8] != b'EFI PART':
            raise ValueError('Missing secondary GPT header')
        hs, crc = struct.unpack_from('<II', backup, 12)
        if not 92 <= hs <= SECTOR:
            raise ValueError('Invalid secondary GPT header size')
        checked = bytearray(backup[:hs]); checked[16:20] = bytes(4)
        if zlib.crc32(checked) != crc:
            raise ValueError('Secondary GPT header CRC mismatch')
        if struct.unpack_from('<QQ', backup, 24) != (last, 1):
            raise ValueError('Secondary GPT header location mismatch')
        if primary[40:72] != backup[40:72] or primary[80:92] != backup[80:92]:
            raise ValueError('GPT copies disagree')
        table_lba, count, entry_size, table_crc = struct.unpack_from('<QIII', primary, 72)
        backup_lba = struct.unpack_from('<Q', backup, 72)[0]
        table_size = count*entry_size
        usable_end = struct.unpack_from('<Q', primary, 48)[0]
        if not usable_end < backup_lba or backup_lba*SECTOR+table_size > last*SECTOR:
            raise ValueError('Invalid secondary GPT table location')
        table = read_at(f, table_lba*SECTOR, table_size)
        if table != read_at(f, backup_lba*SECTOR, table_size) or zlib.crc32(table) != table_crc:
            raise ValueError('Secondary GPT table mismatch')
    return {p['name']: p for p in parts}


class GPTView:
    """Read-only GPT-only disk view; avoids a 7.6 GB temporary file on NTFS."""
    def __init__(self, size, primary, secondary):
        self.size = size; self.position = 0
        self.regions = [(0, primary), (size-len(secondary), secondary)]

    def seek(self, position):
        self.position = position

    def read(self, length):
        length = max(0, min(length, self.size-self.position))
        result = bytearray(length)
        for start, data in self.regions:
            low = max(start, self.position)
            high = min(start+len(data), self.position+length)
            if low < high:
                result[low-self.position:high-self.position] = data[low-start:high-start]
        self.position += length
        return bytes(result)


def check_stock(dump, profile):
    dump = Path(dump)
    parts = validate_gpt(dump)
    if dump.stat().st_size != profile['disk_bytes']:
        raise ValueError('This eMMC capacity has not been validated; no writes performed')
    geometry = [[p['name'], p['first_lba'], p['last_lba']] for p in parts.values()]
    if geometry != profile['geometry']:
        raise ValueError('Not the known factory UFI001C layout; no writes performed')
    with dump.open('rb') as f:
        if copy_and_hash(f, parts['modem']) != profile['stock_modem_sha256']:
            raise ValueError('Different factory radio/board revision; requires a separate compatibility check')
        hashes = {name: copy_and_hash(f, parts[name]) for name in NV}
        for name in ANCHORS:
            data = read_at(f, parts[name]['offset_bytes'], parts[name]['size_bytes'])
            if data == bytes(len(data)) or data == b'\xff'*len(data):
                raise ValueError('Empty identity/NV partition: ' + name)
    return parts, hashes


def check_base(base):
    base = Path(base)
    manifest = json.loads((base / 'base-manifest.json').read_text(encoding='utf-8'))
    required = {'aboot.bin', 'gpt_both0.bin', 'hyp.mbn', 'rpm.mbn',
                'sbc_1.0_8016.bin', 'sbl1.mbn', 'tz.mbn'}
    if set(manifest['files']) != required:
        raise ValueError('Invalid bootloader file manifest')
    for name, entry in manifest['files'].items():
        path = base / name
        if path.stat().st_size != entry['bytes'] or update.sha(path) != entry['sha256']:
            raise ValueError('Base image checksum mismatch: ' + name)


def firmware_files(source, out):
    """Copy only named image/data files, never ZIP paths or embedded programs."""
    source, out = Path(source), Path(out)
    out.mkdir()
    if source.is_dir():
        for name, cap in LIMITS.items():
            path = source / name
            if path.is_file():
                if path.stat().st_size > cap: raise ValueError('Oversized file: ' + name)
                shutil.copyfile(path, out / name)
    else:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > 2000: raise ValueError('Unexpected firmware archive')
            found = {}
            for info in infos:
                path = PurePosixPath(info.filename.replace('\\', '/'))
                if path.name not in LIMITS: continue
                if path.is_absolute() or '..' in path.parts or info.is_dir():
                    raise ValueError('Invalid firmware archive path')
                if path.name in found: raise ValueError('Duplicate image/data file in ZIP: ' + path.name)
                if info.file_size > LIMITS[path.name] or info.flag_bits & 1:
                    raise ValueError('Oversized/encrypted archive entry')
                found[path.name] = info
            if len({PurePosixPath(i.filename.replace('\\', '/')).parent for i in found.values()}) > 1:
                raise ValueError('Image/data files are from different archive folders')
            for name, info in found.items():
                with archive.open(info) as src, (out / name).open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024**2)
    if not all((out / n).is_file() for n in ('boot.img', 'system.img', 'SHA256SUMS', 'build.config', 'ACCESS.txt')):
        raise ValueError('Choose the complete successful v3 XHTTP firmware ZIP (not source-code ZIP)')
    config = (out / 'build.config').read_text(encoding='utf-8-sig')
    for setting in ('CONFIG_PACKAGE_ufi001c-radio=y', 'CONFIG_PACKAGE_ufi-care=y',
                    'CONFIG_PACKAGE_podkop-engine=y'):
        if setting not in config.splitlines():
            raise ValueError('This is not the v3 XHTTP build: missing ' + setting)
    report_path = out / 'xhttp-validation.json'
    if not report_path.is_file():
        raise ValueError('Missing mandatory xhttp-validation.json from the successful v3 build')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if (report.get('status') != 'passed' or report.get('architecture') != 'aarch64'
            or report.get('engine_version') != '1.13.21-pdk-r12'
            or not {'transport.xhttp', 'tools.decode-link'} <= set(report.get('features', []))):
        raise ValueError('The mandatory ARM64 XHTTP build gate did not pass')
    for name in ('boot.img', 'system.img'):
        if report.get('image_sha256', {}).get(name) != update.sha(out / name):
            raise ValueError('XHTTP validation report is from a different image: ' + name)
    return out


def target_gpt(template, disk_bytes, out):
    """Materialize the upstream size-less template for this exact eMMC.

    Keep its 14-partition ordering (root=mmcblk0p14), types and attributes.
    Produce both full 32-sector table areas and headers, CRCs included.
    """
    source = Path(template).read_bytes()
    if len(source) != 67*SECTOR: raise ValueError('Unexpected GPT template length')
    count, entry_size = struct.unpack_from('<II', source, SECTOR+80)
    if (count, entry_size) != (16, 128): raise ValueError('Unexpected GPT template dimensions')
    table = bytearray(source[2*SECTOR:34*SECTOR])
    total = disk_bytes // SECTOR
    if disk_bytes % SECTOR or total < 659456+2097152+34:
        raise ValueError('Disk is too small or not sector aligned')
    last_usable = total-34
    disk_guid = uuid.uuid4().bytes_le
    for i, (name, (start, capacity)) in enumerate(update.EXPECTED.items()):
        e = table[i*128:(i+1)*128]
        if e[56:128].decode('utf-16-le').split('\0')[0] != name:
            raise ValueError('GPT template partition order mismatch')
        first, last = struct.unpack_from('<QQ', e, 32)
        if first != start or (capacity and last-first+1 != capacity//SECTOR):
            raise ValueError('GPT template partition geometry mismatch')
        table[i*128+16:i*128+32] = uuid.uuid4().bytes_le
        if name == 'rootfs': struct.pack_into('<Q', table, i*128+40, last_usable)
    table_crc = zlib.crc32(table[:count*entry_size])
    headers = []
    for current, other, entries in ((1, total-1, 2), (total-1, 1, total-33)):
        h = bytearray(source[SECTOR:2*SECTOR])
        struct.pack_into('<QQQQ', h, 24, current, other, 34, last_usable)
        h[56:72] = disk_guid
        struct.pack_into('<QIII', h, 72, entries, count, entry_size, table_crc)
        struct.pack_into('<I', h, 16, 0)
        struct.pack_into('<I', h, 16, zlib.crc32(h[:92]))
        headers.append(h)
    mbr = bytearray(source[:SECTOR])
    struct.pack_into('<I', mbr, 458, min(total-1, 0xffffffff))
    primary = Path(out) / 'gpt-primary.bin'
    secondary = Path(out) / 'gpt-secondary.bin'
    primary.write_bytes(mbr + headers[0] + table)
    secondary.write_bytes(table + headers[1])
    view = GPTView(disk_bytes, primary.read_bytes(), secondary.read_bytes())
    parts = validate_gpt(view, disk_bytes)
    update.check_layout(list(parts.values()))
    return parts, primary, secondary


def pad_copy(source, destination, size):
    source, destination = Path(source), Path(destination)
    if source.stat().st_size > size: raise ValueError('Partition overflow: ' + source.name)
    with source.open('rb') as src, destination.open('xb') as dst:
        shutil.copyfileobj(src, dst)
        remaining = size-dst.tell()
        while remaining:
            take = min(remaining, 1024**2); dst.write(bytes(take)); remaining -= take


def make_plan(dump, profile, base, work, prepared):
    """Build every byte to write before starting a device mutation."""
    dump, base, work = Path(dump), Path(base), Path(work)
    stock, hashes = check_stock(dump, profile)
    check_base(base)
    target, primary, secondary = target_gpt(base / 'gpt_both0.bin', dump.stat().st_size, work)
    writes = []
    def add(name, path, start, capacity):
        path = Path(path)
        size = path.stat().st_size
        if not 0 < size <= capacity or size % SECTOR:
            raise ValueError('Invalid write length: ' + name)
        writes.append({'name': name, 'file': str(path.resolve()), 'first_lba': start,
                       'sectors': size//SECTOR, 'sha256': update.sha(path)})
    # Rootfs and boot first. The GPT switch is committed only after all payloads
    # and bootchain components have been read back and verified.
    for name, path in prepared:
        add(name, path, target[name]['first_lba'], target[name]['size_bytes'])
    with dump.open('rb') as src:
        for name in NV:
            data = read_at(src, stock[name]['offset_bytes'], stock[name]['size_bytes'])
            path = work / (name + '.bin')
            capacity = target[name]['size_bytes']
            if len(data) > capacity: raise ValueError('NV destination too small: ' + name)
            path.write_bytes(data + bytes(capacity-len(data)))
            add(name, path, target[name]['first_lba'], capacity)
    # No donor devinfo/unlock state is copied. A blank devinfo is initialized by
    # the OpenStick bootloader, as for a freshly erased Android device-info area.
    devinfo = work / 'devinfo.bin'; devinfo.write_bytes(bytes(target['devinfo']['size_bytes']))
    add('devinfo', devinfo, target['devinfo']['first_lba'], target['devinfo']['size_bytes'])
    for name, filename in (('aboot','aboot.bin'), ('hyp','hyp.mbn'), ('tz','tz.mbn'),
                           ('rpm','rpm.mbn'), ('sbl1','sbl1.mbn'), ('cdt','sbc_1.0_8016.bin')):
        path = work / (name + '.edl.bin')
        pad_copy(base / filename, path, target[name]['size_bytes'])
        add(name, path, target[name]['first_lba'], target[name]['size_bytes'])
    add('gpt-secondary', secondary, dump.stat().st_size//SECTOR-33, 33*SECTOR)
    add('gpt-primary', primary, 0, 34*SECTOR)
    spans = sorted((w['first_lba'], w['first_lba']+w['sectors'], w['name']) for w in writes)
    for a, b in zip(spans, spans[1:]):
        if a[1] > b[0]: raise ValueError('Overlapping planned writes: '+a[2]+'/'+b[2])
    for start, end, name in spans:
        if start < 0 or end*SECTOR > dump.stat().st_size: raise ValueError('Write outside disk: '+name)
        for anchor in ANCHORS:
            part = stock[anchor]
            if start <= part['last_lba'] and end > part['first_lba']:
                raise ValueError('Write would destroy the recovery identity anchor')
    return writes, stock, hashes


class EDL:
    def __init__(self, root, log):
        self.root = Path(root).resolve()
        self.exe = self.root / ('.venv/Scripts/python.exe' if sys.platform == 'win32' else '.venv/bin/python')
        self.log = Path(log)
        if not self.exe.is_file() or not (self.root / 'edl.py').is_file():
            raise ValueError('Working EDL environment not found. Set UFI_EDL_ROOT to its folder.')

    def __call__(self, action, *args):
        # EDL may return success even when a programmer rejects a write.
        # Every mutation therefore MUST be followed by an independent readback.
        command = [str(self.exe), '-u', str(self.root / 'edl.py'), action,
                   *map(str, args), '--memory=emmc', '--sectorsize=512']
        with self.log.open('ab') as log:
            log.write(('\nEDL: ' + action + '\n').encode()); log.flush()
            with subprocess.Popen(command, cwd=self.root, stdout=log, stderr=subprocess.STDOUT) as proc:
                started = time.monotonic()
                try:
                    while True:
                        try:
                            code = proc.wait(timeout=30)
                            if code: raise subprocess.CalledProcessError(code, command)
                            break
                        except subprocess.TimeoutExpired:
                            elapsed = int(time.monotonic()-started)
                            suffix = ''
                            if action in ('rf', 'rs') and Path(args[-1]).exists():
                                suffix = f', считано {Path(args[-1]).stat().st_size//1048576} МБ'
                            print(f'  EDL {action}: {elapsed//60} мин {elapsed%60} с{suffix}; журнал: {self.log}', flush=True)
                            if action in ('rf', 'rs') and elapsed >= 180 and (not Path(args[-1]).exists() or Path(args[-1]).stat().st_size == 0):
                                raise ValueError('За 3 минуты чтение не началось. Проверьте EDL/9008, драйвер и edl.log.')
                            if elapsed > 45*60:
                                raise ValueError('EDL timeout; inspect edl.log and keep the backup')
                except BaseException:
                    proc.terminate()
                    try: proc.wait(timeout=10)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait()
                    raise


def bind_device(edl, dump, stock, work, check_gpt=True):
    """Fail closed when another modem was connected since the backup."""
    with Path(dump).open('rb') as src:
        for name in ANCHORS:
            part = stock[name]
            current = Path(work) / ('current-' + name + '.bin')
            if current.exists(): current.unlink()
            edl('rs', part['first_lba'], part['size_bytes']//SECTOR, current)
            if current.stat().st_size != part['size_bytes'] or update.sha(current) != copy_and_hash(src, part):
                raise ValueError('Connected modem does not match this backup: '+name)
        if check_gpt:
            current = Path(work) / 'current-gpt.bin'
            if current.exists(): current.unlink()
            edl('rs', 0, 34, current)
            if current.read_bytes() != read_at(src, 0, 34*SECTOR):
                raise ValueError('Device GPT changed since backup')


def execute_plan(edl, writes, journal_path, work):
    journal = json.loads(Path(journal_path).read_text(encoding='utf-8'))
    journal['status'] = 'writing'; journal['verified_writes'] = []
    save_json(journal_path, journal)
    for i, item in enumerate(writes, 1):
        print(f"[{i}/{len(writes)}] Запись и проверка: {item['name']}", flush=True)
        image = Path(item['file'])
        if image.stat().st_size != item['sectors']*SECTOR or update.sha(image) != item['sha256']:
            raise ValueError('Prepared image changed before write: '+item['name'])
        journal['current_write'] = item['name']; save_json(journal_path, journal)
        edl('ws', item['first_lba'], image)
        readback = Path(work) / 'readback.bin'
        # Do not allow a failed reader to leave a previous successful file behind.
        if readback.exists(): readback.unlink()
        edl('rs', item['first_lba'], item['sectors'], readback)
        if readback.stat().st_size != item['sectors']*SECTOR or update.sha(readback) != item['sha256']:
            raise ValueError('READBACK MISMATCH: '+item['name']+'; keep EDL mode, use RESTORE.cmd')
        journal['verified_writes'].append(item['name']); save_json(journal_path, journal)
        readback.unlink()
    journal['status'] = 'written-and-readback-verified'
    journal.pop('current_write', None); save_json(journal_path, journal)


def select_path(title, initial, mode='zip'):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
        result = filedialog.askopenfilename(title=title, initialdir=str(initial),
                    filetypes=[('ZIP прошивки', '*.zip')] if mode == 'zip' else [('Резервная копия', 'emmc.bin')])
        root.destroy()
        if not result: raise ValueError('Выбор отменён')
        return Path(result)
    except ImportError:
        value = input(title + '\nПолный путь к файлу: ').strip().strip('"')
        if not value: raise ValueError('Выбор отменён')
        return Path(value)


def new_run(parent):
    parent = Path(parent); parent.mkdir(parents=True, exist_ok=True)
    path = parent / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    path.mkdir(); return path


def confirm(text, word):
    print(text, flush=True)
    if input('Для продолжения введите ' + word + ': ').strip() != word:
        raise ValueError('Отменено; запись не началась')


def install(args):
    base = HERE / 'stock-base'; profile = load_profile(base); check_base(base)
    source = args.firmware or (HERE if (HERE/'boot.img').is_file() and (HERE/'system.img').is_file()
                              else select_path('Выберите ZIP успешной сборки UFI001C v3 XHTTP', Path.home()/'Downloads'))
    run = new_run(args.backups)
    print('Резервная копия и журнал: ' + str(run), flush=True)
    # A full backup plus rootfs preparation/readback needs substantially more
    # space than the compressed firmware ZIP.
    if shutil.disk_usage(run).free < profile['disk_bytes'] + 4*GIB:
        raise ValueError('Нужно не менее 12 ГБ свободного места на диске с бэкапом')
    images = firmware_files(source, run / 'images')
    work = run / 'prepared'; work.mkdir()
    prepared = update.prepare_images(images, work)
    edl = EDL(args.edl_root, run / 'edl.log')
    print('Оставьте подключённым только один модем. Выключите его, зажмите кнопку,\n'
          'подключите к USB ПК и отпустите кнопку. Это вход в EDL (9008).', flush=True)
    input('После подключения нажмите Enter. Сначала выполняется только чтение: ')
    preview = run / 'stock-gpt-preview.bin'
    edl('rs', 0, 34, preview)
    preview_parts = read_gpt(io.BytesIO(preview.read_bytes()), profile['disk_bytes'], SECTOR)
    geometry = [[p['name'], p['first_lba'], p['last_lba']] for p in preview_parts]
    if geometry != profile['geometry']:
        raise ValueError('Этот запуск предназначен для заводского UFI001C. Текущая разметка другая; запись не началась.')
    dump = run / 'emmc.bin'
    print('Чтение всей заводской памяти. Может занять 10–20 минут. Журнал: edl.log', flush=True)
    edl('rf', dump)
    with dump.open('r+b') as saved:
        saved.flush(); os.fsync(saved.fileno())
    stock, nv_hashes = check_stock(dump, profile)
    report = {'format': 1, 'installer': VERSION, 'profile': profile['id'],
              'backup_bytes': dump.stat().st_size, 'backup_sha256': update.sha(dump),
              'nv_sha256': nv_hashes, 'status': 'backup-verified',
              'meaning': 'GPT copies/CRC, expected length, profile and saved SHA256 checked; not a second full-device read'}
    journal = run / 'install.json'; save_json(journal, report)
    print('БЭКАП СОХРАНЁН И ПРОВЕРЕН: ' + str(dump), flush=True)
    writes, stock, _ = make_plan(dump, profile, base, work, prepared)
    save_json(run / 'write-plan.json', writes)
    bind_device(edl, dump, stock, work)
    confirm('Найден известный заводской профиль UFI001C / UFI001BC 20211121, 8 ГБ.\n'
            'Проверьте маркировку платы: UFI001C. Одинакового корпуса недостаточно.\n'
            'Будут заменены разметка, загрузчики и система. Заводские настройки\n'
            'станут недоступны; индивидуальные NV этого модема перенесутся из бэкапа.\n'
            'Это первая аппаратная проверка автоматического перехода RC1 со стока.\n'
            'Поддерживайте питание до завершения. Откат: RESTORE.cmd и этот emmc.bin.', 'UFI001C')
    bind_device(edl, dump, stock, work)  # also binds device after the user prompt
    execute_plan(edl, writes, journal, work)
    # Check the untouched identity anchors after all writes as a recovery guard.
    bind_device(edl, dump, stock, work, check_gpt=False)
    print('\nЗАПИСЬ И ЧТЕНИЕ ОБРАТНО СОВПАЛИ. Аппаратная загрузка ещё не проверена.\n'
          'Отключите USB и включите модем БЕЗ зажатой кнопки. Подождите 2–3 минуты.\n'
          'Подключитесь к Wi-Fi из ACCESS.txt; адрес панели: http://192.168.1.1', flush=True)
    if (images / 'ACCESS.txt').is_file():
        print('\n' + (images / 'ACCESS.txt').read_text(encoding='utf-8-sig'), flush=True)
        shutil.copyfile(images / 'ACCESS.txt', run / 'ACCESS.txt')
        if sys.platform == 'win32': os.startfile(str(run / 'ACCESS.txt'))


PACKAGE_FILES = ('stock_install.py', 'ufi-install.py', 'radio_backup.py',
                 'INSTALL.cmd', 'RESTORE.cmd', 'MAKE-BUNDLE.cmd',
                 'README-STOCK-RU.md', 'VALIDATION-STOCK.md', 'BASE-SOURCES.md')


def bundle(args):
    """Build a single distribution ZIP from the existing successful firmware.

    No recompilation, USB use, personal backup or NV files in this package.
    """
    source = args.firmware or select_path('Выберите ZIP успешной сборки UFI001C v3 XHTTP', Path.home()/'Downloads')
    output = args.output or Path.home()/'Downloads'/('UFI001C-OpenWrt25-USB-'+VERSION+'.zip')
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists(): raise ValueError('Файл уже существует: '+str(output)+'; переименуйте его или выберите другой --output')
    if shutil.disk_usage(output.parent).free < 3*GIB: raise ValueError('Need 3 GB free to validate and package images')
    check_base(HERE/'stock-base')
    with tempfile.TemporaryDirectory(prefix='ufi-bundle-', dir=output.parent) as tmp:
        work=Path(tmp); images=firmware_files(source, work/'images')
        prepared=work/'prepared'; prepared.mkdir()
        update.prepare_images(images, prepared)
        building=work/'bundle.zip'
        with zipfile.ZipFile(building, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            prefix='UFI001C-USB/'
            for name in PACKAGE_FILES: archive.write(HERE/name, prefix+name)
            for path in sorted((HERE/'stock-base').iterdir()):
                if path.is_file(): archive.write(path, prefix+'stock-base/'+path.name)
            for path in sorted(images.iterdir()):
                archive.write(path, prefix+path.name)
        with zipfile.ZipFile(building) as archive:
            if archive.testzip() is not None: raise ValueError('Generated ZIP integrity check failed')
        # Exclusive creation, so an existing release is never overwritten.
        with building.open('rb') as src, output.open('xb') as dst:
            shutil.copyfileobj(src, dst)
    checksum=update.sha(output)
    output.with_suffix('.zip.sha256').write_text(checksum+'  '+output.name+'\n', encoding='ascii')
    print('ГОТОВ ЕДИНЫЙ АРХИВ: '+str(output)+'\nSHA256: '+checksum+'\n'
          'Для следующего стокового модема: распаковать этот ZIP и запустить INSTALL.cmd.\n'
          'Полные бэкапы и индивидуальные NV в архив не включены.', flush=True)
    if sys.platform == 'win32': os.startfile(str(output.parent))


def restore(args):
    profile = load_profile(HERE / 'stock-base')
    dump = args.backup or select_path('Выберите заводской emmc.bin ЭТОГО модема', args.backups, 'backup')
    dump = Path(dump).resolve(strict=True)
    report = json.loads((dump.parent / 'install.json').read_text(encoding='utf-8'))
    if report.get('format') != 1 or report.get('profile') != profile['id']:
        raise ValueError('This is not a stock backup made by this installer')
    if dump.stat().st_size != report['backup_bytes'] or update.sha(dump) != report['backup_sha256']:
        raise ValueError('Backup checksum mismatch; restoration refused')
    stock, hashes = check_stock(dump, profile)
    if hashes != report['nv_sha256']: raise ValueError('Backup NV report mismatch')
    work = new_run(dump.parent / 'restore-logs')
    if shutil.disk_usage(work).free < dump.stat().st_size + GIB:
        raise ValueError('Need at least 9 GB free for a full restoration readback')
    edl = EDL(args.edl_root, work / 'edl.log')
    print('Подключите только этот модем с зажатой кнопкой (EDL), затем отпустите её.')
    input('Нажмите Enter для проверки принадлежности бэкапа: ')
    bind_device(edl, dump, stock, work, check_gpt=False)
    confirm('Возвращается вся заводская память из выбранного бэкапа.\n'
            'Все изменения после создания этой копии будут потеряны.', 'RESTORE')
    bind_device(edl, dump, stock, work, check_gpt=False)
    recovery = work / 'restore.json'
    save_json(recovery, {'status': 'writing', 'backup_sha256': report['backup_sha256']})
    print('Восстановление всей памяти. Не отключайте USB.', flush=True)
    edl('wf', dump)
    print('Повторное чтение всей памяти для проверки…', flush=True)
    readback = work / 'readback.bin'; edl('rf', readback)
    if readback.stat().st_size != dump.stat().st_size or update.sha(readback) != report['backup_sha256']:
        raise ValueError('Full restore readback differs; keep EDL mode, inspect edl.log')
    save_json(recovery, {'status': 'full-readback-verified', 'backup_sha256': report['backup_sha256']})
    readback.unlink()
    print('СТОК ВОССТАНОВЛЕН И СВЕРЕН. Переподключите USB без кнопки.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'restore', 'bundle'])
    parser.add_argument('--version', action='version', version='UFI stock installer '+VERSION)
    parser.add_argument('--edl-root', type=Path, default=Path(os.environ.get('UFI_EDL_ROOT', str(Path.home()/'edl'))))
    parser.add_argument('--backups', type=Path, default=Path.home()/'UFI001C-backups')
    parser.add_argument('--firmware', type=Path)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print('UFI001C: простой переход со стока — '+VERSION, flush=True)
    try:
        {'install': install, 'restore': restore, 'bundle': bundle}[args.action](args)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        print('\nSTOP: '+str(exc)+'\nЕсли запись уже начиналась, сохраните EDL и используйте RESTORE.cmd.\n'
              'Бэкапы находятся в '+str(args.backups)+'. Не удаляйте их.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nПрервано. Если запись началась, используйте RESTORE.cmd; сохраните бэкап.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
