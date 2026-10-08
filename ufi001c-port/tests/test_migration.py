import importlib.util
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

HERE=Path(__file__).resolve().parent
MIGRATION=HERE/'project/migration' if (HERE/'project/migration').is_dir() else HERE.parent/'migration'
sys.path.insert(0,str(MIGRATION))
spec=importlib.util.spec_from_file_location('ufi_install',MIGRATION/'ufi-install.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def sparse(chunks,blocks):
    data=struct.pack('<I4H4I',0xed26ff3a,1,0,28,12,4096,blocks,len(chunks),0)
    for kind,count,payload in chunks:
        data+=struct.pack('<2H2I',kind,0,count,12+len(payload))+payload
    return data

def boot(board=b'thwc,ufi001c\0',root=b'root=/dev/mmcblk0p14 rw rootwait'):
    kernel=bytearray(64);kernel[56:60]=b'ARM\x64'
    enc=zlib.compressobj(wbits=31);gz=enc.compress(kernel)+enc.flush()
    dtb=struct.pack('>II',0xd00dfeed,40+len(board))+b'\0'*32+board
    body=gz+dtb
    data=bytearray(2048);data[:8]=b'ANDROID!'
    struct.pack_into('<10I',data,8,len(body),0,0,0,0,0,0,2048,0,0)
    data[64:64+len(root)]=root
    data+=body
    data+=b'\0'*((-len(data))%2048)
    return bytes(data)


def metadata(payload, info=None):
    if info is None:
        info={'metadata_version':'1.1','supported_devices':['openstick-ufi001c'],
              'version':{'release':'25.12.5','target':'msm89xx/msm8916'}}
    body=b'\0'*8+json.dumps(info).encode()+b'\n'
    combined=payload+body
    return combined+struct.pack('>IIB3sI',0x46577830,zlib.crc32(combined)^0xffffffff,1,b'\0'*3,len(body)+16)


def fixtures(folder):
    raw=bytearray(4096);raw[1080:1082]=b'\x53\xef'
    files={'boot.img':metadata(boot()),'system.img':metadata(sparse([(0xcac1,1,raw)],1))}
    for name,data in files.items():(folder/name).write_bytes(data)
    (folder/'SHA256SUMS').write_text(''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in files.items()))
    return files

class Checks(unittest.TestCase):
    def test_sparse_expansion(self):
        raw=bytearray(4096);raw[1080:1082]=b'\x53\xef'
        data=sparse([(0xcac1,1,raw),(0xcac2,1,b'ABCD'),(0xcac3,1,b'')],3)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'in').write_bytes(data)
            self.assertEqual(m.expand_sparse(p/'in',p/'out',12288),12288)
            self.assertEqual((p/'out').read_bytes(),raw+b'ABCD'*1024+b'\0'*4096)

    def test_sparse_rejects_truncation_and_overflow(self):
        raw=bytearray(4096);raw[1080:1082]=b'\x53\xef'
        for data in [sparse([(0xcac1,1,raw)],1)[:-1],sparse([(0xcac3,2,b'')],1)]:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d);(p/'in').write_bytes(data)
                with self.assertRaises(ValueError):m.expand_sparse(p/'in',p/'out',4096)

    def test_boot_identity_and_root(self):
        m.check_boot(boot())
        for data in [boot(board=b'wrong,device\0'),boot(root=b'root=/dev/mmcblk0p12 rootwait'),boot()[:2050]]:
            with self.assertRaises(ValueError):m.check_boot(data)

    def test_layout_refuses_stock_and_modified(self):
        good=[{'name':n,'first_lba':v[0],'size_bytes':v[1] or 4096} for n,v in m.EXPECTED.items()]
        self.assertIn('rootfs',m.check_layout(good))
        with self.assertRaises(ValueError):m.check_layout([{'name':'modem','first_lba':131072,'size_bytes':67108864}])
        good[-1]['first_lba']+=1
        with self.assertRaises(ValueError):m.check_layout(good)

    def test_appended_openwrt_metadata_and_sector_padding(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);files=fixtures(p);(p/'work').mkdir()
            self.assertNotEqual(len(files['boot.img'])%512,0)
            images=dict(m.prepare_images(p,p/'work'))
            self.assertEqual(images['boot'].stat().st_size%512,0)
            self.assertEqual(images['boot'].read_bytes()[:len(files['boot.img'])],files['boot.img'])
            self.assertEqual(images['rootfs'].stat().st_size,4096)
            for name,data in files.items():self.assertEqual((p/name).read_bytes(),data)

    def test_invalid_image_stops_before_edl(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);fixtures(p);dump=p/'emmc.bin';dump.write_bytes(b'test backup')
            parts=[{'name':n,'first_lba':v[0],'offset_bytes':v[0]*512,'size_bytes':v[1] or 4096} for n,v in m.EXPECTED.items()]
            dump.with_suffix('.json').write_text(json.dumps({'bytes':dump.stat().st_size,'sha256':m.sha(dump)}))
            with (p/'system.img').open('ab') as f:f.write(b'junk')
            argv=['ufi-install.py','update','--images',str(p),'--backup',str(dump)]
            with patch.object(sys,'argv',argv),patch.object(m,'partitions',return_value=parts),patch.object(m,'read_at',side_effect=lambda f,offset,length: b'G'*(34*512) if offset==0 else boot()),patch.object(m,'edl_command') as edl:
                with self.assertRaisesRegex(ValueError,'checksum mismatch'):m.main()
                edl.assert_not_called()
            self.assertFalse(list(p.glob('verify-*')))

    def test_unknown_trailer_and_bad_fwtool_crc_rejected(self):
        raw=bytearray(4096);raw[1080:1082]=b'\x53\xef'
        original=sparse([(0xcac1,1,raw)],1)
        badcrc=bytearray(metadata(original));badcrc[100]^=1
        cases=[original+b'junk',metadata(original)+b'\0'*10,bytes(badcrc),metadata(original)[:-1]]
        for data in cases:
            with self.subTest(tail=data[-16:]),tempfile.TemporaryDirectory() as d:
                p=Path(d);(p/'in').write_bytes(data)
                with self.assertRaises(ValueError):m.expand_sparse(p/'in',p/'out',4096)

    def test_image_hash_and_metadata_pair_checked(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);files=fixtures(p);(p/'work').mkdir()
            (p/'boot.img').write_bytes(files['boot.img']+b'\0')
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):m.prepare_images(p,p/'work')
            self.assertEqual(list((p/'work').iterdir()),[])
            (p/'boot.img').write_bytes(files['boot.img'])
            (p/'system.img').write_bytes(metadata(files['system.img'][:4136],{'version':'wrong-build'}))
            (p/'SHA256SUMS').write_text(''.join(m.sha(p/n)+'  '+n+'\n' for n in files))
            with self.assertRaisesRegex(ValueError,'metadata differs'):m.prepare_images(p,p/'work')

    @unittest.skipUnless(os.environ.get('FWTOOL_TEST_BINARY'),'set FWTOOL_TEST_BINARY for upstream compatibility test')
    def test_real_fwtool_output(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);files=fixtures(p)
            (p/'metadata.json').write_text(json.dumps({'metadata_version':'1.1','supported_devices':['openstick-ufi001c']}))
            fwtool=os.environ['FWTOOL_TEST_BINARY']
            for name in files:
                end,_=m.openwrt_payload(p/name)
                original=(p/name).read_bytes()[:end];(p/name).write_bytes(original)
                subprocess.run([fwtool,'-I',str(p/'metadata.json'),str(p/name)],check=True)
                self.assertEqual(m.openwrt_payload(p/name)[0],len(original))
            (p/'SHA256SUMS').write_text(''.join(m.sha(p/n)+'  '+n+'\n' for n in files))
            (p/'work').mkdir();m.prepare_images(p,p/'work')
            # Check optional signature envelope framing, not crypto authentication.
            (p/'sig').write_bytes(b'test signature envelope')
            subprocess.run([fwtool,'-S',str(p/'sig'),str(p/'boot.img')],check=True)
            self.assertIsInstance(m.openwrt_payload(p/'boot.img')[1],dict)

    def test_update_uses_prepared_images_and_verifies_readback(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);files=fixtures(p);dump=p/'emmc.bin';dump.write_bytes(b'test backup')
            parts=[{'name':n,'first_lba':v[0],'offset_bytes':v[0]*512,'size_bytes':v[1] or 4096} for n,v in m.EXPECTED.items()]
            nv={n:b'N'*4096 for n in ('modemst1','modemst2','fsg')}
            saved_gpt=b'G'*(34*512)
            dump.with_suffix('.json').write_text(json.dumps({'bytes':dump.stat().st_size,'sha256':m.sha(dump),'nv_sha256':{n:hashlib.sha256(data).hexdigest() for n,data in nv.items()}}))
            calls=[];written={}
            def fake_edl(root,action,*args):
                calls.append((action,args))
                if action=='w':written[args[0]]=Path(args[1]).read_bytes()
                elif action=='rs':
                    start,count,dest=args
                    if start==0:data=saved_gpt
                    else:
                        name=next(part['name'] for part in parts if part['first_lba']==start)
                        data=nv[name] if name in nv else written[name]
                    Path(dest).write_bytes(data)
                else:self.fail('Unexpected EDL action '+action)
            argv=['ufi-install.py','update','--images',str(p),'--backup',str(dump)]
            with patch.object(sys,'argv',argv),patch.object(m,'partitions',return_value=parts),patch.object(m,'read_at',side_effect=lambda f,offset,length: saved_gpt if offset==0 else boot()),patch.object(m,'edl_command',side_effect=fake_edl):
                m.main()
            self.assertEqual(list(written),['rootfs','boot'])
            self.assertEqual(written['boot'][:len(files['boot.img'])],files['boot.img'])
            self.assertEqual(len(written['boot'])%512,0)
            self.assertEqual([call[0] for call in calls],['rs']*4+['w','rs','w','rs'])
            for name,data in files.items():self.assertEqual((p/name).read_bytes(),data)

if __name__=='__main__':unittest.main()
