import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
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

if __name__=='__main__':unittest.main()
