"""NTP hang scenario: sync_time_ntp() must never block podkop startup.

BusyBox `ntpd -q` has no built-in timeout, so if NTP servers are unreachable
it hangs forever. sync_time_ntp() bounds it to 30 seconds (shell-native, no
extra packages) and always succeeds. These tests stub ntpd/sleep/log and run
the real function extracted from usr/bin/podkop.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

PODKOP = Path(__file__).resolve().parents[1] / 'overlay/package/openstick/podkop/files/usr/bin/podkop'


def function_body():
    text = PODKOP.read_text()
    fn = text.split('sync_time_ntp() {', 1)[1]
    lines = []
    for line in fn.splitlines()[1:]:
        if line == '}':
            break
        lines.append(line)
    return '\n'.join(lines)


class NtpHangTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'log.txt'
        self.ntpd = self.root / 'ntpd-stub'
        # Near-instant sleep: the 30-iteration bound must not cost 30 real seconds,
        # but each tick must be long enough for a fast ntpd stub to start+exit.
        self.driver_head = (
            'log() { printf "%%s\\n" "$*" >> "%s"; }\n'
            'sleep() { command sleep 0.05; }\n' % self.log
        )

    def run_sync(self, ntpd_body):
        self.ntpd.write_text('#!' + sys.executable + '\n' + ntpd_body)
        self.ntpd.chmod(0o755)
        body = function_body().replace('/usr/sbin/ntpd', str(self.ntpd))
        driver = self.driver_head + 'sync_time_ntp() {\n' + body + '\n}\nsync_time_ntp\n'
        script = self.root / 'run.sh'
        script.write_text(driver)
        env = dict(os.environ)
        start = time.monotonic()
        p = subprocess.run(['sh', str(script)], env=env, text=True,
                           capture_output=True, timeout=15)
        elapsed = time.monotonic() - start
        return p, elapsed

    def logged(self):
        return self.log.read_text() if self.log.exists() else ''

    def test_hung_ntp_is_killed_and_startup_continues(self):
        # ntpd that never answers: busy loop, immune to the instant-sleep stub.
        p, elapsed = self.run_sync('import time\nwhile True: time.sleep(60)\n')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertLess(elapsed, 10, 'NTP hang must not block startup')
        self.assertIn('timed out', self.logged())

    def test_successful_ntp_sync_is_quiet(self):
        p, elapsed = self.run_sync('import sys\nsys.exit(0)\n')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn('timed out', self.logged())


if __name__ == '__main__':
    unittest.main()
