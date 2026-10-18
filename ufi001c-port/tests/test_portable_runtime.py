"""Offline guards for the native launcher and release/runtime binding."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

MIGRATION = Path(__file__).resolve().parents[1] / 'migration'
sys.path.insert(0, str(MIGRATION))
import portable_runtime as portable
import runtime_manifest as manifest
import stock_install as stock


def make_runtime(root, source=MIGRATION, commit='a' * 40):
    root = Path(root)
    for name in ['UFI001C-Installer.exe', 'edl/UFI-EDL.exe', 'Drivers/zadig-2.8.exe']:
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'MZ' + bytes(1022))  # test fixture, never a distributed executable
    (root / 'EDL-SOURCE.zip').write_bytes(b'fixture')
    path = root / 'edl/_internal/edlclient/Windows/libusb-1.0.dll'
    path.parent.mkdir(parents=True); path.write_bytes(b'fixture DLL')
    value = {'format': 1, 'version': stock.VERSION, 'platform': 'windows-x86_64',
             'source_commit': commit, 'source_sha256': manifest.source_hashes(source),
             'smoke_tests': {'installer': 'passed', 'edl': 'passed', 'edl_help': 'passed'},
             'files': manifest.inventory(root)}
    (root / 'runtime-manifest.json').write_text(json.dumps(value))
    return root


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_runtime_bound_to_exact_source_and_commit(self):
        runtime = make_runtime(self.root / 'runtime')
        manifest.verify_runtime(runtime, MIGRATION, 'a' * 40)
        with self.assertRaisesRegex(ValueError, 'another source commit'):
            manifest.verify_runtime(runtime, MIGRATION, 'b' * 40)
        value = json.loads((runtime / 'runtime-manifest.json').read_text())
        value['source_sha256']['stock_install.py'] = '0' * 64
        (runtime / 'runtime-manifest.json').write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'different installer source'):
            manifest.verify_runtime(runtime, MIGRATION)

    def test_modified_missing_and_extra_files_cannot_be_released(self):
        for case in ['modified', 'missing', 'extra']:
            with self.subTest(case=case):
                runtime = make_runtime(self.root / case)
                if case == 'modified': (runtime / 'UFI001C-Installer.exe').write_bytes(b'MZbad')
                elif case == 'missing': (runtime / 'edl/UFI-EDL.exe').unlink()
                else: (runtime / 'unexpected.bin').write_bytes(b'not in manifest')
                with self.assertRaisesRegex(ValueError, 'checksum/file-list'):
                    manifest.verify_runtime(runtime, MIGRATION)

    def test_smoke_failures_and_traversal_are_rejected(self):
        for case in ['smoke', 'traversal']:
            runtime = make_runtime(self.root / case)
            value = json.loads((runtime / 'runtime-manifest.json').read_text())
            if case == 'smoke': value['smoke_tests']['edl'] = 'failed'
            else: value['files']['../outside'] = {'bytes': 1, 'sha256': '0' * 64}
            (runtime / 'runtime-manifest.json').write_text(json.dumps(value))
            with self.assertRaises(ValueError): manifest.verify_runtime(runtime, MIGRATION)

    def test_portable_edl_uses_native_exe_and_one_usb_precheck(self):
        root = self.root / 'Папка с пробелами' / 'edl'; root.mkdir(parents=True)
        (root / 'UFI-EDL.exe').write_bytes(b'fixture')
        with patch.object(portable, 'bundled_edl', return_value=root), \
             patch.object(portable, 'require_single_edl') as check, patch.object(stock, 'run_edl') as run:
            edl = stock.EDL(self.root / 'unused', self.root / 'log')
            edl('rs', 0, 34, self.root / 'preview.bin')
            edl('rs', 100, 4, self.root / 'read.bin')
            self.assertEqual(check.call_count, 1)
            command = run.call_args_list[0].args[0]
            self.assertEqual(command[:4], [str(root / 'UFI-EDL.exe'), 'rs', '0', '34'])
            self.assertNotIn('-u', command)
            self.assertTrue(command[-2:] == ['--memory=emmc', '--sectorsize=512'])

    def test_legacy_edl_environment_remains_supported(self):
        edl_root = self.root / 'edl'; (edl_root / '.venv/Scripts').mkdir(parents=True)
        (edl_root / '.venv/Scripts/python.exe').write_bytes(b'fixture')
        (edl_root / 'edl.py').write_text('fixture')
        with patch.object(portable, 'bundled_edl', return_value=None), \
             patch.object(stock.sys, 'platform', 'win32'), patch.object(stock, 'run_edl') as run:
            stock.EDL(edl_root, self.root / 'log')('rs', 0, 1, self.root / 'out')
            self.assertEqual(run.call_args.args[0][:4],
                             [str(edl_root / '.venv/Scripts/python.exe'), '-u', str(edl_root / 'edl.py'), 'rs'])

    def test_usb_precheck_does_not_claim_reset_or_write_any_device(self):
        usb = types.ModuleType('usb'); core = types.ModuleType('usb.core')
        core.USBError = type('USBError', (Exception,), {})
        usb.core = core
        device = Mock()
        with patch.dict(sys.modules, {'usb': usb, 'usb.core': core}), \
             patch.object(portable, 'usb_backend', return_value=object()):
            core.find = Mock(return_value=iter([device]))
            portable.require_single_edl()
            self.assertEqual(device.mock_calls, [])
            for devices in [[], [device, device]]:
                core.find = Mock(return_value=iter(devices))
                with self.assertRaises(ValueError): portable.require_single_edl()

    def test_missing_portable_tree_does_not_fall_back_silently(self):
        exe = self.root / 'runtime/UFI001C-Installer.exe'
        exe.parent.mkdir()
        with patch.object(portable.sys, 'frozen', True, create=True), \
             patch.object(portable.sys, 'executable', str(exe)):
            with self.assertRaisesRegex(ValueError, 'Неполный комплект'):
                portable.bundled_edl()

    def test_frozen_entry_resolves_data_next_to_usb_zip_not_internal_directory(self):
        import windows_entry
        exe=self.root/'USB тест/runtime/UFI001C-Installer.exe'
        with patch.object(windows_entry.sys,'frozen',True,create=True), \
             patch.object(windows_entry.sys,'executable',str(exe)), \
             patch.object(windows_entry.sys,'argv',[str(exe),'--self-test']), \
             patch.object(stock,'HERE',stock.HERE), patch.object(portable,'self_test') as check:
            self.assertEqual(windows_entry.main(),0)
            self.assertEqual(stock.HERE,exe.resolve().parent.parent)
            check.assert_called_once()


if __name__ == '__main__': unittest.main(verbosity=2)
