#!/usr/bin/env python3
"""Guarded EDL backup/update for the existing UFI001C OpenStick layout.

Stock conversion is intentionally refused: no GPT, bootloader or NV writes.
Requires the user's existing, working bkerler/edl installation and EDL USB mode.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib

from radio_backup import read_gpt, read_at, copy_and_hash, export_radio

SECTOR = 512
GIB = 1024**3
EXPECTED = {
    'cdt': (131072, None), 'sbl1': (262144, 0x80000),
    'rpm': (263168, 0x80000), 'tz': (264192, 0x100000),
    'hyp': (266240, 0x80000), 'sec': (267264, 0x4000),
    'modemst1': (267296, 0x200000), 'modemst2': (271392, 0x200000),
    'fsc': (275488, 0x400), 'fsg': (393216, 0x200000),
    'aboot': (524288, 0x100000), 'boot': (526336, 0x4000000),
    'devinfo': (657408, 0x100000), 'rootfs': (659456, None),
}


def sha(path, limit=None):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        left = limit
        while left is None or left:
            b = f.read(1024*1024 if left is None else min(1024*1024, left))
            if not b:
                if left: raise ValueError('Readback file truncated')
                break
            h.update(b)
            if left is not None: left -= len(b)
    return h.hexdigest()


def partitions(dump):
    dump = Path(dump)
    with dump.open('rb') as f:
        parts = read_gpt(f, dump.stat().st_size, SECTOR)
    if len({p['name'] for p in parts}) != len(parts):
        raise ValueError('Duplicate partition names')
    return parts


def check_layout(parts):
    if [p['name'] for p in parts] != list(EXPECTED):
        raise ValueError('Not the verified OpenStick layout. Stock/unknown GPT conversion is NOT supported by update.')
    for p in parts:
        start, length = EXPECTED[p['name']]
        if p['first_lba'] != start or (length is not None and p['size_bytes'] != length):
            raise ValueError('Unexpected partition geometry: '+p['name'])
    return {p['name']: p for p in parts}


def check_boot(data):
    if len(data) > 64*1024**2 or len(data) < 2048 or data[:8] != b'ANDROID!':
        raise ValueError('Invalid/oversized Android boot image')
    fields = struct.unpack_from('<10I', data, 8)
    kernel_size, _, ramdisk, _, second, _, _, page, version, _ = fields
    if page not in (2048,4096,8192) or version or ramdisk or second or page+kernel_size>len(data):
        raise ValueError('Unexpected boot image layout')
    cmd = data[64:576].split(b'\0')[0] + data[608:1632].split(b'\0')[0]
    if b'root=/dev/mmcblk0p14' not in cmd or b'rootwait' not in cmd:
        raise ValueError('Wrong root partition in boot command line')
    dec=zlib.decompressobj(31)
    kernel=dec.decompress(data[page:page+kernel_size],256*1024**2)
    if not dec.eof or kernel[56:60]!=b'ARM\x64':
        raise ValueError('Not the supported gzip ARM64 kernel')
    dtb=dec.unused_data
    if len(dtb)<40 or dtb[:4]!=b'\xd0\x0d\xfe\xed':
        raise ValueError('Missing appended device tree')
    size=struct.unpack_from('>I',dtb,4)[0]
    if not 40<=size<=len(dtb) or b'thwc,ufi001c\0' not in dtb[:size]:
        raise ValueError('Image is not for UFI001C')


def expand_sparse(source, target, capacity):
    """Expand Android sparse image, materializing skipped blocks as zeros."""
    source, target = Path(source), Path(target)
    with source.open('rb') as f:
        head=f.read(28)
        if len(head)!=28: raise ValueError('Truncated sparse header')
        magic,major,minor,hs,cs,bs,blocks,chunks,crc=struct.unpack('<I4H4I',head)
        total=blocks*bs
        if magic!=0xed26ff3a or major!=1 or not 28<=hs<=4096 or not 12<=cs<=4096:
            raise ValueError('Unsupported sparse format')
        if bs!=4096 or not 4096<=total<=min(capacity,GIB) or chunks>1000000:
            raise ValueError('Invalid or oversized rootfs')
        f.seek(hs); done=0
        with target.open('xb') as out:
            for _ in range(chunks):
                header=f.read(cs)
                if len(header)!=cs: raise ValueError('Truncated chunk header')
                kind,_,count,length=struct.unpack_from('<2H2I',header)
                size=count*bs
                if done+size>total: raise ValueError('Sparse output overflow')
                if kind==0xcac1:
                    if length!=cs+size: raise ValueError('Bad RAW length')
                    left=size
                    while left:
                        b=f.read(min(left,1024**2))
                        if not b: raise ValueError('Truncated RAW chunk')
                        out.write(b); left-=len(b)
                elif kind in (0xcac2,0xcac3):
                    if length!=cs+(4 if kind==0xcac2 else 0): raise ValueError('Bad FILL/SKIP length')
                    word=f.read(4) if kind==0xcac2 else b'\0'*4
                    if len(word)!=4: raise ValueError('Truncated FILL value')
                    buf=word*(1024**2//4);left=size
                    while left:
                        take=min(left,len(buf));out.write(buf[:take]);left-=take
                elif kind==0xcac4:
                    if count or length!=cs+4 or len(f.read(4))!=4: raise ValueError('Bad CRC chunk')
                else: raise ValueError('Unknown sparse chunk')
                done+=size
            if done!=total or f.read(1): raise ValueError('Sparse size/trailer mismatch')
    with target.open('rb') as out:
        out.seek(1080)
        if out.read(2)!=b'\x53\xef': raise ValueError('Rootfs is not ext4')
    return total


def edl_command(root, *args):
    root=Path(root).resolve()
    exe=root/'.venv/Scripts/python.exe' if sys.platform=='win32' else root/'.venv/bin/python'
    if not exe.is_file() or not (root/'edl.py').is_file():
        raise ValueError('Existing edl venv not found; pass --edl-root')
    subprocess.run([str(exe),'-u',str(root/'edl.py'),*map(str,args),'--memory=emmc','--sectorsize=512'],cwd=root,check=True)


def write_report(dump):
    parts=partitions(dump)
    with dump.open('rb') as f:
        nv={p['name']:copy_and_hash(f,p) for p in parts if p['name'] in ('modemst1','modemst2','fsg','fsc')}
    report={'file':dump.name,'bytes':dump.stat().st_size,'sha256':sha(dump),'partitions':parts,'nv_sha256':nv,'sector':SECTOR}
    dump.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','inspect','update'])
    parser.add_argument('--edl-root',type=Path,default=Path.home()/'edl')
    parser.add_argument('--backup',type=Path)
    parser.add_argument('--images',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args()
    folder=args.images.resolve();folder.mkdir(parents=True,exist_ok=True)
    if args.action=='backup':
        if shutil.disk_usage(folder).free<10*GIB: raise ValueError('Need at least 10 GiB free for full eMMC backup')
        out=folder/('backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        out.mkdir();dump=out/'emmc.bin'
        edl_command(args.edl_root,'rf',dump)
        report=write_report(dump)
        if any(p['name']=='modem' for p in report['partitions']): export_radio(dump,out)
        print('BACKUP VERIFIED:',dump)
        print('Keep this full backup private. Use its path with update --backup. No device writes performed.')
        return
    if not args.backup: raise ValueError('--backup path to saved emmc.bin required')
    dump=args.backup.resolve(strict=True)
    parts=partitions(dump)
    if args.action=='inspect':
        for p in parts:print(p['name'],p['first_lba'],p['size_bytes'])
        return
    pmap=check_layout(parts)
    report=json.loads(dump.with_suffix('.json').read_text())
    if report['bytes']!=dump.stat().st_size or report['sha256']!=sha(dump):
        raise ValueError('Backup checksum mismatch')
    with dump.open('rb') as f:
        # Existing boot is independently tied to this board and partition layout.
        check_boot(read_at(f,pmap['boot']['offset_bytes'],pmap['boot']['size_bytes']))
        saved_gpt=read_at(f,0,34*SECTOR)
    sums={}
    for line in (folder/'SHA256SUMS').read_text().splitlines():
        if line.strip():
            digest,name=line.split(maxsplit=1);sums[name.lstrip('*')]=digest
    for name in ('boot.img','system.img'):
        if sums.get(name)!=sha(folder/name): raise ValueError('Image checksum mismatch: '+name)
    boot=(folder/'boot.img').read_bytes();check_boot(boot)
    if len(boot)%SECTOR: raise ValueError('Boot image must be sector aligned')
    if shutil.disk_usage(folder).free<3*GIB: raise ValueError('Need 3 GiB free for expansion and readback')
    temp=folder/('verify-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));temp.mkdir()
    edl_command(args.edl_root,'rs',0,34,temp/'gpt.bin')
    if (temp/'gpt.bin').read_bytes()!=saved_gpt: raise ValueError('Connected device GPT differs from backup')
    for name in ('modemst1','modemst2','fsg'):
        p=pmap[name];readback=temp/(name+'.bin')
        edl_command(args.edl_root,'rs',p['first_lba'],p['size_bytes']//SECTOR,readback)
        if sha(readback)!=report['nv_sha256'][name]:
            raise ValueError('Connected device NV differs from backup: '+name+'; make a fresh backup of THIS device')
        readback.unlink()
    raw=temp/'rootfs.raw'
    length=expand_sparse(folder/'system.img',raw,pmap['rootfs']['size_bytes'])
    print('Preflight passed. Updating ONLY rootfs and boot. Keep USB connected.',flush=True)
    for name,image in [('rootfs',raw),('boot',folder/'boot.img')]:
        expected=sha(image);size=image.stat().st_size
        edl_command(args.edl_root,'w',name,image)
        readback=temp/(name+'.readback')
        edl_command(args.edl_root,'rs',pmap[name]['first_lba'],size//SECTOR,readback)
        if readback.stat().st_size!=size or sha(readback)!=expected:
            raise ValueError('Written image verification FAILED: '+name+'. Keep EDL mode; do not reboot.')
        print('READBACK VERIFIED:',name,flush=True)
        readback.unlink()
    raw.unlink()
    print('UPDATE VERIFIED. Unplug, then reconnect WITHOUT holding the button. New Wi-Fi password: ACCESS.txt.')


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,subprocess.CalledProcessError,zlib.error) as exc:
        print('STOP:',exc,file=sys.stderr);sys.exit(1)
