"""Execute the shipped LuCI settings and DNS button handlers with mocked RPC."""
from pathlib import Path
import shutil
import subprocess
import unittest


class SettingsUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'requires Node.js')
    def test_cloudflare_controls_and_probe_results(self):
        p = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs'))],
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
