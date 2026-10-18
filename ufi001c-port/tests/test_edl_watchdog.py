"""Real subprocess/pipe tests with a fake EDL executable; never accesses USB."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'migration'))
from edl_watchdog import EDLError, run_edl
import stock_install as s


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root/'emmc.bin'
        self.log = self.root/'edl.log'

    def run_child(self, program, action='rf', **options):
        script = self.root/'fake-edl.py'
        script.write_text('import sys,time,os\nfrom pathlib import Path\n'
                          'out=Path(sys.argv[1])\n'+program, encoding='utf-8')
        defaults = dict(idle_timeout=0.3, total_timeout=5, poll_interval=0.01,
                        log_limit=4096, status_interval=30)
        defaults.update(options)
        args = (0, 1, self.output) if action == 'rs' else (self.output,)
        run_edl([sys.executable, '-u', str(script), str(self.output)],
                self.root, action, args, self.log, **defaults)

    def report(self):
        return json.loads((self.root/'edl-failure.json').read_text(encoding='utf-8'))

    def test_success_preserves_file_and_log(self):
        self.run_child("out.write_bytes(b'A'*512)\nprint('complete')\n")
        self.assertEqual(self.output.read_bytes(), b'A'*512)
        self.assertIn(b'complete', self.log.read_bytes())
        self.assertFalse((self.root/'edl-failure.json').exists())

    def test_stalled_partial_read_is_stopped_and_preserved(self):
        started = time.monotonic()
        with self.assertRaises(EDLError):
            self.run_child("out.write_bytes(b'A'*512)\ntime.sleep(30)\n")
        self.assertLess(time.monotonic()-started, 4)
        self.assertEqual(self.output.stat().st_size, 512)
        self.assertEqual(self.report()['reason'], 'read-stalled')
        self.assertEqual(self.report()['output_bytes'], 512)
        self.assertFalse(self.report()['operation_confirmed'])

    def test_no_initial_data_is_stopped(self):
        with self.assertRaises(EDLError):
            self.run_child("time.sleep(30)\n")
        self.assertEqual(self.report()['reason'], 'read-stalled')
        self.assertEqual(self.report()['output_bytes'], 0)

    def test_growing_file_resets_idle_deadline(self):
        self.run_child("with out.open('wb', buffering=0) as f:\n"
                       " for i in range(8):\n  f.write(b'G'*512)\n  time.sleep(0.08)\n")
        self.assertEqual(self.output.stat().st_size, 4096)

    def test_usb_error_flood_stops_fast_and_log_is_bounded(self):
        started = time.monotonic()
        with self.assertRaises(EDLError):
            self.run_child("out.write_bytes(b'A'*512)\n"
                           "print('USBError(32, Pipe error)', flush=True)\n"
                           "while True:\n os.write(1, b'USBError(5, Input/Output Error)\\n'*4096)\n")
        self.assertLess(time.monotonic()-started, 4)
        self.assertLessEqual(self.log.stat().st_size, 4096+256)
        self.assertEqual(self.report()['reason'], 'usb-error')
        self.assertIn('USBError(32', self.report()['usb_error'])
        self.assertEqual(self.output.stat().st_size, 512)

    def test_split_usb_error_is_detected(self):
        with self.assertRaises(EDLError):
            self.run_child("os.write(1,b'USBErr')\ntime.sleep(0.04)\n"
                           "os.write(1,b'or(5, Input/Output Error)\\n')\ntime.sleep(30)\n")
        self.assertEqual(self.report()['reason'], 'usb-error')

    def test_usb_error_with_zero_exit_is_not_success(self):
        with self.assertRaises(EDLError):
            self.run_child("out.write_bytes(b'X'*512)\nprint('USBError(5, I/O)')\n", action='rs')
        self.assertEqual(self.report()['reason'], 'usb-error')
        self.assertEqual(self.report()['action'], 'rs')

    def test_first_error_after_log_cap_is_kept_in_small_report(self):
        with self.assertRaises(EDLError):
            self.run_child("os.write(1,b'p'*65536)\nprint('USBError(32, Pipe error)')\ntime.sleep(30)\n")
        self.assertTrue(self.report()['log_truncated'])
        self.assertIn('USBError(32', self.report()['usb_error'])
        self.assertLess(self.log.stat().st_size, 4400)

    def test_write_is_not_subject_to_read_idle_timeout(self):
        self.run_child("time.sleep(0.4)\nprint('write complete')\n",
                       action='ws', idle_timeout=0.1)
        self.assertFalse((self.root/'edl-failure.json').exists())

    def test_large_successful_output_is_drained_without_deadlock(self):
        self.run_child("os.write(1,b'a'*(1024*1024))\nout.write_bytes(b'B'*512)\n")
        self.assertEqual(self.output.stat().st_size, 512)
        self.assertLess(self.log.stat().st_size, 4400)

    def test_nonzero_exit_and_total_deadline_are_failures(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_child("sys.exit(7)\n")
        self.assertEqual(self.report()['reason'], 'process-exit')
        with self.assertRaises(EDLError):
            self.run_child("time.sleep(30)\n", action='wf', total_timeout=0.1)
        self.assertEqual(self.report()['reason'], 'total-timeout')

    def test_preflight_usb_failure_cannot_reach_confirmation_or_write(self):
        source = self.root/'firmware'; source.mkdir()
        args = argparse.Namespace(firmware=source, backups=self.root/'backups', edl_root=self.root)
        profile = {'id': 'fixture', 'disk_bytes': 512}
        calls = []
        def edl(action, *params):
            calls.append(action)
            if action == 'rs' and len(calls) > 1:
                Path(params[-1]).write_bytes(b'partial')
                raise EDLError('fixture USBError(32, Pipe error)')
            if action != 'rs':
                self.fail('unexpected device mutation: '+action)
        with patch.object(s, 'check_base'), patch.object(s, 'firmware_files'), \
             patch.object(s.update, 'prepare_images'), \
             patch.object(s, 'preview_profile', return_value=profile), \
             patch.object(s, 'backup_identity', return_value={'regions': []}), \
             patch.object(s.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 100*1024**3})()), \
             patch.object(s, 'EDL', return_value=edl), \
             patch.object(s, 'check_stock') as validate, patch.object(s, 'make_plan') as plan, \
             patch.object(s, 'confirm') as confirm, patch.object(s, 'execute_plan') as write, \
             patch('builtins.input', return_value=''), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(EDLError):
                s.install(args)
        self.assertEqual(calls, ['rs', 'rs'])
        for gate in (validate, plan, confirm, write):
            gate.assert_not_called()
        self.assertIn('НЕ НАЧИНАЛАСЬ', output.getvalue())
        self.assertFalse(list(args.backups.rglob('install.json')))


if __name__ == '__main__':
    unittest.main()
