"""Unit tests for resolve_bootstrap_transport() in ufi_dns_tunnel.sh.

The helper reads podkop.settings.ufi_bootstrap_transport (tcp|udp, default tcp)
and dies on anything else. Both filter_config and install_fix rely on it so the
Yandex bootstrap diagnostics always exercise the configured protocol.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

LIB = Path(__file__).resolve().parents[1] / 'overlay/package/openstick/podkop/files/usr/lib'


def helper_code():
    text = (LIB / 'ufi_dns_tunnel.sh').read_text()
    fn = text.split('resolve_bootstrap_transport() {', 1)[1]
    # Function ends at the first line that is exactly '}'.
    lines = []
    for line in fn.splitlines()[1:]:
        if line == '}':
            break
        lines.append(line)
    body = '\n'.join(lines)
    return ('resolve_bootstrap_transport() {\n' + body + '\n}\n')


class BootstrapTransportTests(unittest.TestCase):
    def run_helper(self, stub_value):
        driver = (
            'setting() { printf "%s" "$TRANSPORT_STUB"; }\n'
            'die() { echo "died: $*" >&2; exit 3; }\n'
            + helper_code() +
            'resolve_bootstrap_transport\n'
        )
        with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as f:
            f.write(driver)
            path = f.name
        try:
            env = dict(os.environ, TRANSPORT_STUB=stub_value)
            p = subprocess.run(['sh', path], env=env, text=True,
                               capture_output=True, timeout=10)
        finally:
            os.unlink(path)
        return p

    def test_empty_setting_defaults_to_tcp(self):
        p = self.run_helper('')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), 'tcp')

    def test_explicit_udp_and_tcp(self):
        for value in ('udp', 'tcp'):
            p = self.run_helper(value)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.strip(), value)

    def test_invalid_setting_dies(self):
        for value in ('invalid', 'TCP', '0'):
            p = self.run_helper(value)
            self.assertNotEqual(p.returncode, 0, value)
            self.assertIn('Invalid bootstrap DNS transport', p.stderr)


if __name__ == '__main__':
    unittest.main()
