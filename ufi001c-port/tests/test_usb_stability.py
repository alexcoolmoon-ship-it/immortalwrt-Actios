"""USB disconnect events must not rebuild the gadget by default."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

FILES = Path(__file__).resolve().parents[1] / 'overlay/package/openstick/gc/files'


class USBStabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name)
        (self.p/'role').write_text('gadget\n')
        (self.p/'uptime').write_text('100.00 100.00\n')
        (self.p/'uci').write_text('''#!/bin/sh
case "$*" in
*auto_restart) echo "${TEST_AUTO:-0}";;
*config.enabled) echo 1;;
*config.mode) echo gadget;;
esac
''')
        (self.p/'gc').write_text('#!/bin/sh\necho restart >> "$TEST_CALLS"\n')
        (self.p/'logger').write_text('#!/bin/sh\nexit 0\n')
        for name in ('uci','gc','logger'): (self.p/name).chmod(0o755)
        code=(FILES/'restart_gc.sh').read_text()
        code=code.replace('/sys/class/udc/ci_hdrc.0/device/driver/ci_hdrc.0/role',str(self.p/'role'))
        code=code.replace('/var/run/',str(self.p)+'/').replace('/proc/uptime',str(self.p/'uptime'))
        code=code.replace('/etc/init.d/gc',str(self.p/'gc'))
        (self.p/'restart.sh').write_text(code)
        self.env=dict(os.environ,PATH=str(self.p)+os.pathsep+os.environ['PATH'],TEST_CALLS=str(self.p/'calls'))

    def invoke(self, enabled='0'):
        return subprocess.run(['sh',str(self.p/'restart.sh')],env=dict(self.env,TEST_AUTO=enabled),capture_output=True,text=True)

    def test_default_and_missing_option_never_restart_usb(self):
        for state in ('0',''):
            for _ in range(3):
                r=self.invoke(state); self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse((self.p/'calls').exists())

    def test_explicit_opt_in_has_lock_and_monotonic_cooldown(self):
        self.assertEqual(self.invoke('1').returncode,0)
        self.assertEqual(self.invoke('1').returncode,0)
        self.assertEqual((self.p/'calls').read_text().splitlines(),['restart'])
        (self.p/'uptime').write_text('165.00 0\n')
        (self.p/'ufi-gc-restart.lock').mkdir()
        self.assertEqual(self.invoke('1').returncode,0)
        self.assertEqual((self.p/'calls').read_text().splitlines(),['restart'])
        (self.p/'ufi-gc-restart.lock').rmdir()
        self.assertEqual(self.invoke('1').returncode,0)
        self.assertEqual((self.p/'calls').read_text().splitlines(),['restart','restart'])

    def test_monitor_does_not_start_unless_user_opted_in(self):
        script='\n'.join((FILES/'hotplug-gcreboot.init').read_text().splitlines()[1:])
        for state in ('0','1'):
            code=script+'\nprocd_open_instance() { echo started; }; procd_set_param() { :; }; procd_close_instance() { :; }; start_service\n'
            p=subprocess.run(['sh','-c',code],env=dict(self.env,TEST_AUTO=state),capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual('started' in p.stdout,state=='1')

if __name__ == '__main__': unittest.main()
