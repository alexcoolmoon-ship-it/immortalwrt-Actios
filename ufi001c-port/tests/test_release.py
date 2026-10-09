"""Release packaging rejects reports from a stale rootfs before making a ZIP."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

PORT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT))
import package_release
from test_migration import fixtures
from test_stock_install import firmware_metadata


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / 'images'
        self.images.mkdir()
        fixtures(self.images)
        firmware_metadata(self.images)
        self.report = {
            'status': 'passed', 'profile': 'UFI001C v3.1.2 Setup / OpenWrt 25.12.5',
            'files_sha256': {dest: hashlib.sha256((PORT / source).read_bytes()).hexdigest()
                             for dest, source in package_release.FILES.items()}}
        self.save_report()
        (self.images / 'kernel-routing-check.txt').write_text('PASS: built kernel supports IPv4 policy routing for Podkop\n')

    def save_report(self):
        (self.images / 'routing-rootfs-check.json').write_text(json.dumps(self.report))

    def test_stale_ui_or_missing_kernel_gate_prevents_packaging(self):
        package_release.check_reports(self.images)
        self.report['files_sha256']['usr/lib/podkop/ufi_fakeip_check.sh'] = '0' * 64
        self.save_report()
        with self.assertRaisesRegex(ValueError, 'different source'):
            package_release.check_reports(self.images)
        (self.images / 'kernel-routing-check.txt').write_text('FAIL')
        self.report['files_sha256']['usr/lib/podkop/ufi_fakeip_check.sh'] = hashlib.sha256(
            (PORT / package_release.FILES['usr/lib/podkop/ufi_fakeip_check.sh']).read_bytes()).hexdigest()
        self.save_report()
        with self.assertRaisesRegex(ValueError, 'Kernel policy'):
            package_release.check_reports(self.images)

    def test_single_zip_has_installer_images_reports_but_no_private_files(self):
        (self.images / 'emmc.bin').write_bytes(b'private full backup must not be packed')
        (self.images / 'podkop.saved').write_bytes(b'private proxy must not be packed')
        (self.images / 'INSTALL-RU.md').write_text('test instruction')
        output = self.root / 'release.zip'
        p = subprocess.run([sys.executable, str(PORT / 'package_release.py'), str(self.images),
                            str(output), '--commit', 'a' * 40], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            names = {Path(n).name for n in archive.namelist()}
            self.assertTrue({'INSTALL.cmd','RESTORE.cmd','boot.img','system.img','ACCESS.txt',
                             'routing-rootfs-check.json','release-info.json','INSTALL-RU.md'} <= names)
            self.assertFalse({'emmc.bin','podkop.saved'} & names)
            info = json.loads(archive.read('UFI001C-USB/release-info.json'))
            self.assertEqual(info['hardware_acceptance'], 'pending')
            self.assertEqual(info['source_commit'], 'a' * 40)
        self.assertTrue(output.with_suffix('.zip.sha256').is_file())
