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
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
import zlib

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location('ufi_update', HERE / 'ufi-install.py')
update = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update)
from radio_backup import read_at, read_gpt, copy_and_hash
from edl_watchdog import run_edl, EDLError
from chunk_backup import full_backup, read_range, exclusive
from radio_report import summarize as summarize_radio

VERSION = '3.2.3-clean'
SECTOR = 512
GIB = 1024**3
NV = ('modemst1', 'modemst2', 'fsg', 'fsc', 'sec')
# These two original areas are left untouched throughout conversion. They bind
# recovery to the physical unit, including after a partial GPT/bootloader write.
ANCHORS = ('modemst1', 'modemst2')
LIMITS = {'boot.img': 64*1024**2, 'system.img': GIB,
          'SHA256SUMS': 262144, 'ACCESS.txt': 16384, 'build.config': 2*1024**2,
          'xhttp-validation.json': 65536, 'addons.lock.json': 65536,
          'routing-rootfs-check.json': 65536, 'kernel-routing-check.txt': 16384,
          'dns-runtime-check.txt': 65536,
          'validation.json': 65536, 'release-info.json': 16384,
          'INSTALL-RU.md': 65536, 'VALIDATION-v3.1.2.md': 65536,
          'VALIDATION-v3.1.3.md': 65536, 'VALIDATION-v3.1.4.md': 65536, 'VALIDATION-v3.1.5.md': 65536, 'VALIDATION-v3.1.6.md': 65536,
          'VALIDATION-v3.2.0.md': 65536, 'VALIDATION-v3.2.1.md': 65536, 'VALIDATION-v3.2.2.md': 65536, 'VALIDATION-v3.2.3.md': 65536}


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)


def load_profile(base, profile_id=None):
    if profile_id is None:
        return json.loads((Path(base) / 'stock-profile.json').read_text(encoding='utf-8'))
    profiles = [json.loads(p.read_text(encoding='utf-8'))
                for p in sorted(Path(base).glob('stock-profile*.json'))]
    matches = [p for p in profiles if p['id'] == profile_id]
    if len(matches) != 1:
        raise ValueError('Unknown/duplicate stock profile: ' + str(profile_id))
    return matches[0]


def preview_profile(preview, base):
    """Match exact known geometry after CRC validation, never resize a profile.

    Capacity here is only the GPT claim. check_stock() independently checks the
    complete physical dump length and both GPT copies before permitting writes.
    """
    data = Path(preview).read_bytes()
    if len(data) != 34*SECTOR or data[SECTOR:SECTOR+8] != b'EFI PART':
        raise ValueError('Incomplete/invalid primary GPT read; no writes performed')
    disk_bytes = (struct.unpack_from('<Q', data, SECTOR+32)[0] + 1)*SECTOR
    parts = read_gpt(io.BytesIO(data), disk_bytes, SECTOR)
    geometry = [[p['name'], p['first_lba'], p['last_lba']] for p in parts]
    profiles = [json.loads(p.read_text(encoding='utf-8'))
                for p in sorted(Path(base).glob('stock-profile*.json'))]
    matches = [p for p in profiles if p['disk_bytes'] == disk_bytes and p['geometry'] == geometry]
    if len(matches) != 1:
        raise ValueError(f'Неизвестная заводская разметка ({disk_bytes} байт по GPT). '
                         'Запись не началась. Сохраните stock-gpt-preview.bin и edl.log.')
    return matches[0]


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
            raise ValueError('Different factory radio partition hash; board revision is NOT established. '
                             'No writes started. Run CHECK-BACKUP.cmd on the saved emmc.bin.')
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
    settings = set(config.splitlines())
    for setting in ('CONFIG_PACKAGE_ufi001c-radio=y', 'CONFIG_PACKAGE_podkop-engine=y'):
        if setting not in settings:
            raise ValueError('This is not the v3.2.3 clean XHTTP build: missing ' + setting)
    # Clean firmware deliberately excludes the old recovery services. Requiring
    # ufi-care here used to reject the real clean .config at the last ZIP gate.
    for package in ('sing-box', 'sing-box-tiny', 'podkop-engine-full',
                    'ufi-care', 'ufi-lte-health', 'ufi-service-proxy', 'zapret-ufi'):
        if 'CONFIG_PACKAGE_' + package + '=y' in settings:
            raise ValueError('Unexpected package in clean firmware: ' + package)
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
        from portable_runtime import bundled_edl
        self.portable = bundled_edl()
        self.root = self.portable or Path(root).resolve()
        self.exe = (self.root / 'UFI-EDL.exe' if self.portable else
                    self.root / ('.venv/Scripts/python.exe' if sys.platform == 'win32' else '.venv/bin/python'))
        self.log = Path(log)
        self.checked_usb = False
        if not self.exe.is_file() or (not self.portable and not (self.root / 'edl.py').is_file()):
            raise ValueError('Working EDL environment not found. Set UFI_EDL_ROOT to its folder.')

    def __call__(self, action, *args):
        # EDL may return success even when a programmer rejects a write.
        # Every mutation therefore MUST be followed by an independent readback.
        if self.portable and not self.checked_usb:
            from portable_runtime import require_single_edl
            require_single_edl()
            self.checked_usb = True
        prefix = [str(self.exe)] if self.portable else [str(self.exe), '-u', str(self.root / 'edl.py')]
        command = [*prefix, action,
                   *map(str, args), '--memory=emmc', '--sectorsize=512']
        run_edl(command, self.root, action, args, self.log)


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


def backup_identity(edl, profile, work):
    """Read both GPTs and non-empty unit-specific NV before every resume."""
    size = profile['disk_bytes']
    regions = []
    def read_region(name, start, count):
        path = Path(work) / ('identity-' + name + '.bin')
        path.unlink(missing_ok=True)
        edl('rs', start, count, path)
        if not path.is_file() or path.stat().st_size != count*SECTOR:
            raise ValueError('Incomplete identity read: ' + name)
        data = path.read_bytes()
        regions.append({'name':name, 'offset':start*SECTOR, 'bytes':len(data), 'sha256':digest_bytes(data)})
        return data
    primary = read_region('primary-gpt', 0, 34)
    secondary = read_region('secondary-gpt', size//SECTOR-33, 33)
    parts = validate_gpt(GPTView(size, primary, secondary), size)
    if [[p['name'],p['first_lba'],p['last_lba']] for p in parts.values()] != profile['geometry']:
        raise ValueError('Connected GPT does not match the selected stock profile')
    for name in ANCHORS:
        part = parts[name]
        data = read_region(name, part['first_lba'], part['size_bytes']//SECTOR)
        if data == bytes(len(data)) or data == b'\xff'*len(data):
            raise ValueError('Empty device identity partition: ' + name)
    return {'regions':regions}


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
        read_range(edl, item['first_lba'], item['sectors'], readback)
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
        types = {'zip':[('ZIP прошивки','*.zip')], 'resume':[('Состояние чтения','backup-state.json')],
                 'backup':[('Резервная копия','emmc.bin')]}
        result = filedialog.askopenfilename(title=title, initialdir=str(initial),
                    filetypes=types.get(mode, types['backup']))
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
    with contextlib.ExitStack() as locks:
        return install_locked(args, locks)


def install_locked(args, locks):
    base = HERE / 'stock-base'; check_base(base)
    source = args.firmware
    resume_state = getattr(args, 'resume', None)
    if resume_state:
        resume_state = Path(resume_state).resolve(strict=True)
        if resume_state.name != 'backup-state.json':
            raise ValueError('Select backup-state.json produced by this resumable installer')
        run = resume_state.parent
        locks.enter_context(exclusive(run, 'install.lock'))
        if (run/'install.json').is_file():
            previous = json.loads((run/'install.json').read_text(encoding='utf-8'))
            if previous.get('status') != 'backup-verified' or previous.get('current_write') or previous.get('verified_writes'):
                raise ValueError('Device writing may have started; use RESTORE.cmd, not backup resume')
    else:
        run = new_run(args.backups)
        locks.enter_context(exclusive(run, 'install.lock'))
        source = source or (HERE if (HERE/'boot.img').is_file() and (HERE/'system.img').is_file()
                            else select_path('Выберите ZIP успешной сборки UFI001C v3 XHTTP', Path.home()/'Downloads'))
    print('Резервная копия и журнал: ' + str(run), flush=True)
    # A full backup plus rootfs preparation/readback needs substantially more
    # space than the compressed firmware ZIP.
    if shutil.disk_usage(run).free < 3*GIB:
        raise ValueError('Нужно не менее 3 ГБ для подготовки образов; место под бэкап проверяется после чтения GPT')
    work = run / 'prepared'
    if resume_state:
        # Revalidate the original image pair; do not accumulate another 1 GiB
        # expansion on each retry. Only generated staging dirs are recreated,
        # never backup data. The journal guard above has excluded all writes.
        source = run / 'images'
        if not source.is_dir() or source.is_symlink():
            raise ValueError('Original firmware images for this backup are missing')
        for staging in (run/'images-check', work):
            if staging.is_symlink(): raise ValueError('Unexpected staging symlink')
            if staging.exists(): shutil.rmtree(staging)
        image_out = run / 'images-check'
    else:
        image_out = run / 'images'
    images = firmware_files(source, image_out)
    work.mkdir()
    prepared = update.prepare_images(images, work)
    edl = EDL(args.edl_root, run / 'edl.log')
    print('Оставьте подключённым только один модем. Выключите его, зажмите кнопку,\n'
          'подключите к USB ПК и отпустите кнопку. Это вход в EDL (9008).', flush=True)
    input('После подключения нажмите Enter. Сначала выполняется только чтение: ')
    preview = run / 'stock-gpt-preview.bin'
    edl('rs', 0, 34, preview)
    profile = preview_profile(preview, base)
    print(f"Найдена разметка: {profile['id']}, {profile['disk_bytes']} байт. Проверяем радио до полного бэкапа.", flush=True)
    # Unknown radio stops here, before a multi-gigabyte backup and before all
    # device writes. A completed older backup can be inspected fully offline.
    try:
        radio_preflight(edl, profile, run)
    except (ValueError, OSError, subprocess.CalledProcessError):
        print('Предварительная проверка остановлена. Запись НЕ НАЧИНАЛАСЬ.\n'
              'Полный бэкап в этом запуске ещё не читался. Сохраните stock-probe.json,\n'
              'если он создан, и edl.log. Старые бэкапы не удаляйте.', flush=True)
        raise
    existing_bytes = min((run/'emmc.partial').stat().st_size, profile['disk_bytes']) if (run/'emmc.partial').is_file() else 0
    if (run/'emmc.bin').is_file(): existing_bytes = min((run/'emmc.bin').stat().st_size, profile['disk_bytes'])
    needed = profile['disk_bytes'] - existing_bytes + 4*GIB
    if shutil.disk_usage(run).free < needed:
        raise ValueError(f"Нужно ещё не менее {(profile['disk_bytes']+4*GIB+GIB-1)//GIB} ГиБ свободного места под бэкап и проверку")
    print('Чтение всей заводской памяти блоками по 32 МБ. Журнал: edl.log\n'
          'При USB-обрыве сохранённые проверенные блоки можно продолжить через RESUME.cmd.', flush=True)
    try:
        dump = full_backup(edl, run, profile, lambda: backup_identity(edl, profile, work))
    except (ValueError, OSError, subprocess.CalledProcessError):
        print('Резервное копирование прервано. Запись на модем НЕ НАЧИНАЛАСЬ.\n'
              'emmc.partial — НЕ готовый бэкап. Сохраните всю папку. Для продолжения\n'
              'переподключите ЭТОТ ЖЕ модем в EDL, запустите RESUME.cmd и выберите\n'
              'backup-state.json из этой папки. Другой модем будет отклонён.', flush=True)
        raise
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
    confirm(f"Проверен заводской профиль {profile['id']}.\n"
            'Проверьте маркировку платы: UFI001C. Одинакового корпуса недостаточно.\n'
            'Будут заменены разметка, загрузчики и система. Заводские настройки\n'
            'станут недоступны; индивидуальные NV этого модема перенесутся из бэкапа.\n'
            'Автоматический переход — кандидат: аппаратная проверка этой ревизии ещё нужна.\n'
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


PACKAGE_FILES = ('stock_install.py', 'ufi-install.py', 'radio_backup.py', 'radio_report.py', 'edl_watchdog.py', 'chunk_backup.py',
                 'portable_runtime.py', 'runtime_manifest.py', 'windows_entry.py', 'windows_edl.py', 'windows-runtime.lock.json',
                 'RUN.cmd', 'CHECK-PC.cmd', 'DRIVER-HELP.cmd', 'DRIVER-RU.txt',
                 'CHECK-LTE.cmd', 'ufi-postboot-check.sh',
                 'CHECK-DNS.cmd', 'ufi-dns-check.sh', 'DNS-README-RU.txt',
                 'INSTALL.cmd', 'RESUME.cmd', 'RESTORE.cmd', 'MAKE-BUNDLE.cmd', 'STOCK-CHECK.cmd', 'CHECK-BACKUP.cmd',
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
    runtime = getattr(args, 'windows_runtime', None)
    if runtime is None and (HERE/'runtime').is_dir():
        runtime = HERE/'runtime'
    if runtime:
        from runtime_manifest import verify_runtime
        verify_runtime(runtime, HERE)
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
            if runtime:
                for path in sorted(Path(runtime).rglob('*')):
                    if path.is_file(): archive.write(path, prefix+'runtime/'+path.relative_to(runtime).as_posix())
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
    dump = args.backup or select_path('Выберите заводской emmc.bin ЭТОГО модема', args.backups, 'backup')
    dump = Path(dump).resolve(strict=True)
    report = json.loads((dump.parent / 'install.json').read_text(encoding='utf-8'))
    if report.get('format') != 1:
        raise ValueError('This is not a stock backup made by this installer')
    profile = load_profile(HERE / 'stock-base', report.get('profile', ''))
    if dump.stat().st_size != report['backup_bytes'] or update.sha(dump) != report['backup_sha256']:
        raise ValueError('Backup checksum mismatch; restoration refused')
    stock, hashes = check_stock(dump, profile)
    if hashes != report['nv_sha256']: raise ValueError('Backup NV report mismatch')
    work = new_run(dump.parent / 'restore-logs')
    if shutil.disk_usage(work).free < dump.stat().st_size + GIB:
        raise ValueError(f"Need {(dump.stat().st_size+2*GIB-1)//GIB} GiB free for full restoration readback")
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
    readback = work / 'readback.bin'
    read_range(edl, 0, dump.stat().st_size//SECTOR, readback)
    if readback.stat().st_size != dump.stat().st_size or update.sha(readback) != report['backup_sha256']:
        raise ValueError('Full restore readback differs; keep EDL mode, inspect edl.log')
    save_json(recovery, {'status': 'full-readback-verified', 'backup_sha256': report['backup_sha256']})
    readback.unlink()
    print('СТОК ВОССТАНОВЛЕН И СВЕРЕН. Переподключите USB без кнопки.', flush=True)


def radio_preflight(edl, profile, run, output=None):
    """Verify both GPT copies and radio with bounded reads; never write USB."""
    size = profile['disk_bytes']
    primary = run / 'stock-gpt-preview.bin'
    secondary = run / 'stock-gpt-secondary.bin'
    edl('rs', size//SECTOR-33, 33, secondary)
    if secondary.stat().st_size != 33*SECTOR:
        raise ValueError('Incomplete secondary GPT read')
    parts = validate_gpt(GPTView(size, primary.read_bytes(), secondary.read_bytes()), size)
    radio = run / 'stock-radio.bin'
    read_range(edl, parts['modem']['first_lba'], parts['modem']['size_bytes']//SECTOR, radio)
    report = summarize_radio(radio.read_bytes(), profile)
    report.update(installer=VERSION, gpt_disk_bytes=size,
                  gpt_copies='CRC and table equality verified',
                  writes_performed=False, full_backup_created=False)
    output = output or run / 'stock-probe.json'
    save_json(output, report)
    print('ОТЧЁТ: ' + str(output), flush=True)
    if not report['stock_modem_hash_matches']:
        raise ValueError('SHA256 раздела радио отличается. Это ещё не доказательство другой платы. '
                         'Запись НЕ НАЧИНАЛАСЬ. Пришлите stock-probe.json для проверки совместимости.')
    return report


def check_backup(args):
    """Inspect the existing backup only; no EDL object and no modem required."""
    dump = args.backup or select_path('Выберите сохранённый emmc.bin (модем не нужен)', args.backups, 'backup')
    dump = Path(dump).resolve(strict=True)
    if dump.suffix == '.partial' or not stat.S_ISREG(dump.stat().st_mode):
        raise ValueError('Select a complete regular emmc.bin, not emmc.partial or a device')
    parts = validate_gpt(dump)
    geometry = [[p['name'], p['first_lba'], p['last_lba']] for p in parts.values()]
    profiles = [json.loads(p.read_text(encoding='utf-8'))
                for p in sorted((HERE/'stock-base').glob('stock-profile*.json'))]
    matches = [p for p in profiles if p['disk_bytes'] == dump.stat().st_size and p['geometry'] == geometry]
    if len(matches) != 1:
        raise ValueError('Backup GPT does not match a known stock layout; no files changed')
    part = parts['modem']
    with dump.open('rb') as src:
        data = read_at(src, part['offset_bytes'], part['size_bytes'])
    report = summarize_radio(data, matches[0])
    report.update(installer=VERSION, backup_bytes=dump.stat().st_size,
                  gpt_copies='CRC and table equality verified',
                  scope='Offline GPT and radio check; not a full backup checksum or live-device check',
                  usb_accessed=False, writes_performed=False)
    # Unique local report folder; do not modify the backup or its checkpoints.
    folder = new_run(dump.parent/'compatibility-reports')
    output = folder/'stock-probe.json'
    save_json(output, report)
    # Only the firmware partition, no modemst/fsg/fsc/userdata or other NV.
    archive = folder/'ufi-stock-radio.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr('modem.img', data)
        z.writestr('stock-probe.json', json.dumps(report, indent=2, ensure_ascii=False))
    print('Проверка завершена БЕЗ подключения модема и БЕЗ записи на него.\n'
          'Для проверки совместимости: ' + str(archive) + '\n'
          'Полный emmc.bin отправлять не нужно.\n'
          'Этот отчёт не разрешает установку неизвестного радио.', flush=True)
    if sys.platform == 'win32': os.startfile(str(folder))
    return report


def probe(args):
    """Read only GPT copies and factory radio; never read or export NV data."""
    run = new_run(args.backups)
    print('Диагностика без прошивки: ' + str(run), flush=True)
    print('Подключите только стоковый модем с зажатой кнопкой, затем отпустите её.')
    input('Нажмите Enter для чтения GPT и контрольной суммы радио: ')
    edl = EDL(args.edl_root, run / 'edl.log')
    primary = run / 'stock-gpt-preview.bin'
    edl('rs', 0, 34, primary)
    profile = preview_profile(primary, HERE / 'stock-base')
    if args.output and Path(args.output).exists(): raise ValueError('Report already exists: ' + str(args.output))
    report = radio_preflight(edl, profile, run, args.output)
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    print('Записи в память модема не было. Полный бэкап ещё не создан.', flush=True)


def resume_install(args):
    args.resume = args.resume or select_path('Выберите backup-state.json прерванного чтения', args.backups, 'resume')
    return install(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'resume', 'restore', 'bundle', 'probe', 'check-backup'])
    parser.add_argument('--version', action='version', version='UFI stock installer '+VERSION)
    parser.add_argument('--edl-root', type=Path, default=Path(os.environ.get('UFI_EDL_ROOT', str(Path.home()/'edl'))))
    parser.add_argument('--backups', type=Path, default=Path.home()/'UFI001C-backups')
    parser.add_argument('--firmware', type=Path)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--windows-runtime', type=Path)
    args = parser.parse_args()
    print('UFI001C: простой переход со стока — '+VERSION, flush=True)
    try:
        {'install': install, 'resume': resume_install, 'restore': restore, 'bundle': bundle,
         'probe': probe, 'check-backup': check_backup}[args.action](args)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        if args.action in ('check-backup', 'probe', 'bundle'):
            print('\nSTOP: ' + str(exc) + '\nЭта операция не выполняет запись на модем. '
                  'Сохраните исходный бэкап.', file=sys.stderr)
            return 1
        print('\nSTOP: '+str(exc)+'\nЕсли запись уже начиналась, сохраните EDL и используйте RESTORE.cmd.\n'
              'Бэкапы находятся в '+str(args.backups)+'. Не удаляйте их.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nПрервано. Если запись началась, используйте RESTORE.cmd; сохраните бэкап.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
