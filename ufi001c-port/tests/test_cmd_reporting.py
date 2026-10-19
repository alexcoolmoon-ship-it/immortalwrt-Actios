"""Run the real report line under CMD: a digit before >> is a file descriptor."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

FIX = Path(__file__).resolve().parents[1] / 'cloudflare-fix'


@unittest.skipUnless(os.name == 'nt', 'requires native Windows CMD')
class CmdReportingTests(unittest.TestCase):
    def test_reports_all_ssh_exit_codes_in_paths_with_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'USB тест'
            root.mkdir()
            report = root / 'UFI-CLOUDFLARE.txt'
            script = Path(temp) / 'report.cmd'
            launchers = [(FIX/name, 'UFI_CF_DIR', 'UFI_CF_EXIT')
                         for name in ('APPLY.cmd', 'CHECK.cmd', 'ROLLBACK.cmd')]
            launchers.append((FIX.parent/'migration/CHECK-DNS.cmd', 'UFI_DNS_DIR', 'UFI_DNS_EXIT'))
            for launcher, directory_key, exit_key in launchers:
                name = launcher.name
                lines = [line for line in launcher.read_text(encoding='utf-8').splitlines()
                         if 'echo SSH_EXIT=' in line]
                self.assertEqual(len(lines), 1, name)
                # Execute a batch file just like the shipped launchers. Passing
                # a redirection expression as a list argument adds MSVC quote
                # escapes which CMD does not understand.
                script.write_bytes(('@echo off\r\n'+lines[0]+'\r\n').encode('utf-8'))
                for code in (0, 1, 2, 3, 255):
                    report.unlink(missing_ok=True)
                    p = subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(script)],
                        env={**os.environ, directory_key: str(root), exit_key: str(code)},
                        capture_output=True)
                    self.assertEqual(p.returncode, 0, (name, code, p.stderr))
                    self.assertEqual(report.read_text().strip(), 'SSH_EXIT='+str(code))
