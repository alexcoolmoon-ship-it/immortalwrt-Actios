"""Offline destructive-flow tests. Never connects to a physical USB device."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile
import zlib

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'migration'))
import stock_install as s
from test_migration import fixtures

BASE = s.HERE/'stock-base'


def firmware_metadata(folder):
    """Synthetic CI proof tied to synthetic images; not a hardware-test claim."""
    config = b'CONFIG_PACKAGE_ufi001c-radio=y\nCONFIG_PACKAGE_ufi-care=y\nCONFIG_PACKAGE_podkop-engine=y\n'
    report = {'status': 'passed', 'architecture': 'aarch64',
              'engine_version': '1.13.21-pdk-r12',
              'features': ['transport.xhttp', 'tools.decode-link'],
              'image_sha256': {n: s.update.sha(folder/n) for n in ('boot.img', 'system.img')}}
    data = {'build.config': config, 'xhttp-validation.json': json.dumps(report).encode(),
            'ACCESS.txt': b'Wi-Fi: test fixture only\n'}
    for name, value in data.items(): (folder/name).write_bytes(value)
    return data


def stock_fixture(path, profile):
    """Sparse host-only mock of the known 8GB factory geometry."""
    size = profile['disk_bytes']; total = size//512
    table = bytearray(16384)
    for i, (name, first, last) in enumerate(profile['geometry']):
        off = i*128
        table[off:off+16] = uuid.uuid5(uuid.NAMESPACE_DNS, name).bytes_le
        table[off+16:off+32] = uuid.uuid5(uuid.NAMESPACE_URL, name).bytes_le
        struct.pack_into('<QQ', table, off+32, first, last)
        encoded = name.encode('utf-16-le'); table[off+56:off+56+len(encoded)] = encoded
    headers=[]
    for current, other, lba in ((1,total-1,2),(total-1,1,total-33)):
        h=bytearray(512);h[:8]=b'EFI PART'
        struct.pack_into('<III',h,8,65536,92,0)
        struct.pack_into('<QQQQ',h,24,current,other,34,total-34)
        h[56:72]=bytes(range(16))
        struct.pack_into('<QIII',h,72,lba,128,128,zlib.crc32(table))
        struct.pack_into('<I',h,16,zlib.crc32(h[:92]));headers.append(h)
    mbr=bytearray(512);mbr[510:512]=b'\x55\xaa'
    with path.open('wb') as f:
        f.write(mbr+headers[0]+table)
        f.seek((total-33)*512);f.write(table+headers[1])
        for n,first,last in profile['geometry']:
            if n in s.NV:
                f.seek(first*512);f.write((n.encode()*((last-first+1)*512//len(n)+1))[:(last-first+1)*512])
    return path


class StockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name)
        self.profile = s.load_profile(BASE)
        # Mock radio differs from the real bundled profile; no production gate
        # is disabled. The production profile itself is never changed.
        self.profile['stock_modem_sha256'] = hashlib.sha256(bytes(64*1024**2)).hexdigest()

    def image_pair(self):
        images=self.p/'images';images.mkdir();fixtures(images)
        work=self.p/'prepared';work.mkdir()
        return s.update.prepare_images(images,work),work

    def test_target_gpt_both_copies_capacity_root_order_and_unique_guids(self):
        parts,a,b=s.target_gpt(BASE/'gpt_both0.bin',self.profile['disk_bytes'],self.p)
        self.assertEqual(list(parts)[-1],'rootfs')
        self.assertEqual(len(parts),14)
        self.assertEqual(parts['rootfs']['last_lba'],self.profile['disk_bytes']//512-34)
        self.assertEqual(a.stat().st_size,34*512)
        self.assertEqual(b.stat().st_size,33*512)
        view=s.GPTView(self.profile['disk_bytes'],a.read_bytes(),b.read_bytes())
        self.assertEqual(s.validate_gpt(view,self.profile['disk_bytes']),parts)
        broken=bytearray(b.read_bytes());broken[5]^=1
        with self.assertRaisesRegex(ValueError,'table mismatch'):
            s.validate_gpt(s.GPTView(self.profile['disk_bytes'],a.read_bytes(),broken),self.profile['disk_bytes'])
        old_guid=a.read_bytes()[568:584]
        second=self.p/'second';second.mkdir()
        _,aa,_=s.target_gpt(BASE/'gpt_both0.bin',self.profile['disk_bytes'],second)
        self.assertNotEqual(old_guid,aa.read_bytes()[568:584])

    def test_stock_profile_wrong_radio_and_bad_backup_gpt_are_rejected(self):
        dump=stock_fixture(self.p/'stock.bin',self.profile)
        parts,hashes=s.check_stock(dump,self.profile)
        self.assertEqual(set(hashes),set(s.NV))
        with self.assertRaisesRegex(ValueError,'Different factory radio'):
            s.check_stock(dump,s.load_profile(BASE))
        with dump.open('r+b') as f:
            f.seek(-512+16,2);f.write(bytes(4))
        with self.assertRaisesRegex(ValueError,'CRC mismatch'):
            s.check_stock(dump,self.profile)

    def test_complete_conversion_preserves_own_nv_and_commits_gpt_last(self):
        dump=stock_fixture(self.p/'stock.bin',self.profile)
        device=stock_fixture(self.p/'device.bin',self.profile)
        prepared,work=self.image_pair()
        plan,stock,hashes=s.make_plan(dump,self.profile,BASE,work,prepared)
        self.assertEqual([v['name'] for v in plan][-2:],['gpt-secondary','gpt-primary'])
        self.assertEqual(len(plan),16)
        calls=[]
        def edl(action,*args):
            calls.append((action,args))
            if action=='ws':
                start,src=args
                with device.open('r+b') as f:
                    f.seek(start*512);f.write(Path(src).read_bytes())
            elif action=='rs':
                start,sectors,dest=args
                with device.open('rb') as f:
                    f.seek(start*512);Path(dest).write_bytes(f.read(sectors*512))
            else:self.fail('Unexpected mutation: '+action)
        s.bind_device(edl,dump,stock,work)
        journal=self.p/'install.json';s.save_json(journal,{'status':'backup-verified'})
        s.execute_plan(edl,plan,journal,work)
        target=s.validate_gpt(device)
        s.update.check_layout(list(target.values()))
        with device.open('rb') as f,dump.open('rb') as original:
            for name in s.NV:
                source=s.read_at(original,stock[name]['offset_bytes'],stock[name]['size_bytes'])
                dest=s.read_at(f,target[name]['offset_bytes'],target[name]['size_bytes'])
                self.assertEqual(dest[:len(source)],source)
                self.assertEqual(dest[len(source):],bytes(len(dest)-len(source)))
        s.bind_device(edl,dump,stock,work,check_gpt=False)
        self.assertEqual(json.loads(journal.read_text())['status'],'written-and-readback-verified')
        writes=[args[0] for action,args in calls if action=='ws']
        self.assertEqual(writes[-1],0)
        self.assertEqual(writes[-2],self.profile['disk_bytes']//512-33)
        # A complete mock rollback returns the original GPT and NV (the real
        # restore path additionally hashes every byte of a full readback).
        self.assertEqual(s.validate_gpt(dump),stock)

    def test_readback_failure_stops_before_any_gpt_commit(self):
        dump=stock_fixture(self.p/'stock.bin',self.profile)
        prepared,work=self.image_pair()
        plan,_,_=s.make_plan(dump,self.profile,BASE,work,prepared)
        journal=self.p/'install.json';s.save_json(journal,{'status':'backup-verified'})
        calls=[]
        def edl(action,*args):
            calls.append((action,args))
            if action=='rs':Path(args[-1]).write_bytes(bytes(args[1]*512))
        with self.assertRaisesRegex(ValueError,'READBACK MISMATCH'):
            s.execute_plan(edl,plan,journal,work)
        self.assertEqual(len([a for a in calls if a[0]=='ws']),1)
        self.assertNotIn(0,[a[1][0] for a in calls if a[0]=='ws'])
        self.assertEqual(json.loads(journal.read_text())['verified_writes'],[])

    def test_archive_accepts_data_only_and_rejects_ambiguity_or_traversal(self):
        src=self.p/'src';src.mkdir();files=fixtures(src)
        files.update(firmware_metadata(src))
        files['SHA256SUMS']=(src/'SHA256SUMS').read_bytes()
        archive=self.p/'fw.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for n,data in files.items():z.writestr('firmware/'+n,data)
            z.writestr('firmware/ufi-install.py','raise RuntimeError("MUST NOT RUN")')
        extracted=s.firmware_files(archive,self.p/'extracted')
        self.assertFalse((extracted/'ufi-install.py').exists())
        with zipfile.ZipFile(archive,'a') as z:z.writestr('../boot.img',files['boot.img'])
        with self.assertRaisesRegex(ValueError,'Invalid firmware archive path'):
            s.firmware_files(archive,self.p/'invalid')

    def test_changed_prepared_image_causes_zero_writes(self):
        work=self.p/'work';work.mkdir()
        image=work/'boot';image.write_bytes(bytes(512))
        plan=[{'name':'boot','file':str(image),'first_lba':100,'sectors':1,'sha256':'0'*64}]
        journal=self.p/'install.json';s.save_json(journal,{'status':'backup-verified'})
        calls=[]
        with self.assertRaisesRegex(ValueError,'changed before write'):
            s.execute_plan(lambda *args:calls.append(args),plan,journal,work)
        self.assertEqual(calls,[])

    def test_all_in_one_bundle_contains_only_distribution_files_and_original_images(self):
        source=self.p/'firmware';source.mkdir();original=fixtures(source)
        firmware_metadata(source)
        (source/'emmc.bin').write_bytes(b'PRIVATE BACKUP MUST NOT BE PACKAGED')
        output=self.p/'release.zip'
        args=argparse.Namespace(firmware=source,output=output)
        with patch.object(s,'EDL') as usb:
            s.bundle(args)
            usb.assert_not_called()
        with zipfile.ZipFile(output) as z:
            self.assertIsNone(z.testzip())
            self.assertIn('UFI001C-USB/INSTALL.cmd',z.namelist())
            self.assertIn('UFI001C-USB/RESTORE.cmd',z.namelist())
            for name,data in original.items():self.assertEqual(z.read('UFI001C-USB/'+name),data)
            self.assertFalse(any('emmc.bin' in name or 'modemst1.bin' in name for name in z.namelist()))
        self.assertEqual(output.with_suffix('.zip.sha256').read_text().split()[0],s.update.sha(output))
        with self.assertRaisesRegex(ValueError,'уже существует'):s.bundle(args)

    def test_v2_and_unrelated_xhttp_reports_are_rejected_offline(self):
        source=self.p/'fw';source.mkdir();fixtures(source);firmware_metadata(source)
        config=source/'build.config'
        config.write_text(config.read_text().replace('CONFIG_PACKAGE_podkop-engine=y\n',''))
        with self.assertRaisesRegex(ValueError,'missing CONFIG_PACKAGE_podkop-engine'):
            s.firmware_files(source,self.p/'v2-rejected')
        firmware_metadata(source)
        report_path=source/'xhttp-validation.json';report=json.loads(report_path.read_text())
        report['image_sha256']['boot.img']='0'*64;report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,'different image: boot.img'):
            s.firmware_files(source,self.p/'unrelated-rejected')
        report_path.unlink()
        with self.assertRaisesRegex(ValueError,'Missing mandatory xhttp-validation.json'):
            s.firmware_files(source,self.p/'missing-rejected')

    def test_wrong_modem_cannot_be_used_for_install_or_restore(self):
        dump=stock_fixture(self.p/'stock.bin',self.profile)
        stock=s.validate_gpt(dump)
        calls=[]
        def wrong_device(action,*args):
            self.assertEqual(action,'rs');calls.append(action)
            Path(args[-1]).write_bytes(bytes(args[1]*512))
        with self.assertRaisesRegex(ValueError,'does not match this backup'):
            s.bind_device(wrong_device,dump,stock,self.p,check_gpt=False)
        self.assertEqual(calls,['rs'])

    def test_a_failed_device_read_cannot_reuse_a_previous_identity_file(self):
        dump=stock_fixture(self.p/'stock.bin',self.profile)
        stock=s.validate_gpt(dump)
        with dump.open('rb') as f:
            for name in s.ANCHORS:
                part=stock[name]
                (self.p/('current-'+name+'.bin')).write_bytes(s.read_at(f,part['offset_bytes'],part['size_bytes']))
        with self.assertRaises(FileNotFoundError):
            s.bind_device(lambda *args:None,dump,stock,self.p,check_gpt=False)

    def test_restore_checks_full_hash_and_readback(self):
        # Restore orchestration with a compact synthetic disk; full-size GPT
        # validation and physical anchors are exercised by the other tests.
        data=b'original factory data'*512
        dump=self.p/'emmc.bin';dump.write_bytes(data)
        s.save_json(self.p/'install.json',{'format':1,'profile':self.profile['id'],
            'backup_bytes':len(data),'backup_sha256':s.update.sha(dump),'nv_sha256':{'test':'abc'}})
        args=argparse.Namespace(backup=dump,backups=self.p,edl_root=self.p)
        writes=[]
        def edl(action,*args):
            if action=='wf':writes.append(Path(args[0]).read_bytes())
            elif action=='rf':Path(args[0]).write_bytes(data)
            else:self.fail(action)
        with patch.object(s,'check_stock',return_value=({}, {'test':'abc'})), \
             patch.object(s,'EDL',return_value=edl),patch.object(s,'bind_device'), \
             patch.object(s,'confirm'),patch('builtins.input',return_value=''), \
             patch.object(s.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(30*s.GIB,0,30*s.GIB)):
            s.restore(args)
        self.assertEqual(writes,[data])
        result=list((self.p/'restore-logs').glob('*/restore.json'))
        self.assertEqual(json.loads(result[0].read_text())['status'],'full-readback-verified')
        dump.write_bytes(data[:-1])
        with patch.object(s,'EDL') as factory:
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):s.restore(args)
            factory.assert_not_called()


if __name__=='__main__':unittest.main()
