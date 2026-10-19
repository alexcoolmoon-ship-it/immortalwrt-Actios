"""Run the shipped shell entry point and the real canonical package builder."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CardSwitchTests(unittest.TestCase):
    def run_switch(self, args, missing=None, fail_module=None, shell=None):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            gpio, init, bin_dir = root/'gpio', root/'init', root/'bin'
            init.mkdir(); bin_dir.mkdir()
            pins = ('sel', 'en', 'sel2', 'en2')
            for pin in pins:
                folder = gpio/f'sim:{pin}'; folder.mkdir(parents=True)
                if pin != missing:
                    (folder/'brightness').write_text('9\n')
            calls = root/'calls'
            for name in ('rmmod', 'modprobe'):
                file = bin_dir/name
                file.write_text('#!/bin/sh\necho "'+name+' $*" >> "$CALLS"\nexit ' +
                                ('1' if fail_module == name else '0')+'\n')
                file.chmod(0o755)
            for name in ('rmtfs', 'modemmanager'):
                file = init/name
                file.write_text('#!/bin/sh\necho "'+name+' $*" >> "$CALLS"\n')
                file.chmod(0o755)
            code = (ROOT/'files/etc/cardswitch/cardswitch.sh').read_text()
            code = code.replace('/sys/class/leds', str(gpio)).replace('/etc/init.d', str(init))
            # Some BusyBox builds prefer applets to PATH. Make the isolated
            # copies call absolute test stubs, never real kernel commands.
            for command in ('rmmod', 'modprobe'):
                code = code.replace('\n'+command+' ', '\n'+str(bin_dir/command)+' ')
            script = root/'switch.sh'; script.write_text(code)
            p = subprocess.run((shell or ['dash'])+[str(script)]+args, env={
                **os.environ, 'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],
                'CALLS':str(calls)}, text=True, capture_output=True, timeout=5)
            values = [(gpio/f'sim:{pin}/brightness').read_text().strip()
                      if pin != missing else None for pin in pins]
            return p, values, calls.read_text().splitlines() if calls.exists() else []

    def test_all_slots_select_exactly_one_gpio_in_dash(self):
        for slot in range(1, 5):
            with self.subTest(slot=slot):
                p, values, calls = self.run_switch([str(slot)])
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(values, ['1' if n == slot-1 else '0' for n in range(4)])
                self.assertEqual(calls, ['rmmod qcom-q6v5-mss', 'modprobe qcom-q6v5-mss',
                                         'rmtfs restart', 'modemmanager restart'])

    def test_invalid_arguments_never_touch_gpio_or_reset_radio(self):
        for args in ([], [''], ['0'], ['5'], ['-n'], ['*'], ['2 extra'], ['1','2']):
            with self.subTest(args=args):
                p, values, calls = self.run_switch(args)
                self.assertEqual(p.returncode, 2)
                self.assertEqual(values, ['9']*4)
                self.assertEqual(calls, [])

    def test_missing_gpio_is_rejected_before_any_change(self):
        p, values, calls = self.run_switch(['2'], missing='en2')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(values, ['9','9','9',None])
        self.assertEqual(calls, [])

    def test_module_failure_stops_service_restarts(self):
        p, values, calls = self.run_switch(['2'], fail_module='rmmod')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(calls, ['rmmod qcom-q6v5-mss'])

    @unittest.skipUnless(shutil.which('busybox'), 'BusyBox runs in GitHub Actions')
    def test_slots_and_invalid_input_in_target_shell_family(self):
        for slot in range(1, 5):
            p, values, calls = self.run_switch([str(slot)], shell=['busybox','sh'])
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(values, ['1' if n == slot-1 else '0' for n in range(4)])
        p, values, calls = self.run_switch(['bad'], shell=['busybox','sh'])
        self.assertEqual(p.returncode, 2)
        self.assertEqual(calls, [])


class SetupEntryPointTests(unittest.TestCase):
    def test_root_wrapper_packages_from_canonical_paths_outside_cwd(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)/'repo'; root.mkdir()
            shutil.copyfile(ROOT/'build_setup_fix.py', root/'build_setup_fix.py')
            canonical = root/'ufi001c-port/hotfix'; canonical.mkdir(parents=True)
            shutil.copyfile(ROOT/'ufi001c-port/hotfix/build_setup_fix.py', canonical/'build_setup_fix.py')
            (canonical/'install.sh').write_text('#!/bin/sh\nexit 0\n')
            (root/'payload.txt').write_text('ACTUAL PAYLOAD\n')
            baseline = {'commit':'test-base', 'files':{'usr/bin/test':{
                'source':'payload.txt','old_sha256':'a'*64,'alternate_old_sha256':None,'mode':'755'}}}
            (canonical/'baseline-3.1.1.json').write_text(json.dumps(baseline))
            dest = Path(work)/'outside-cwd'/'output'
            dest.parent.mkdir()
            p = subprocess.run([sys.executable,str(root/'build_setup_fix.py'),str(dest)],
                               cwd=dest.parent, capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 0, p.stderr)
            result = json.loads(p.stdout)
            archive = dest/'ufi-setup312.tar.gz'
            self.assertEqual(result['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest())
            with tarfile.open(archive) as tar:
                self.assertEqual(tar.extractfile('payload/usr/bin/test').read(), b'ACTUAL PAYLOAD\n')
            self.assertTrue((dest/'APPLY.cmd').is_file())
            self.assertTrue((dest/'START-CHECK.cmd').is_file())


if __name__ == '__main__':
    unittest.main()
