"""Exercise the live fix in a relocated filesystem; no host service/network calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

PORT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_package', PORT / 'hotfix/build_setup_fix.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SetupInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.root = self.tmp / 'rootfs'
        out = self.tmp / 'out'
        builder.build(out)
        self.package = self.tmp / 'package'
        self.package.mkdir()
        with tarfile.open(out / 'ufi-setup312.tar.gz') as archive:
            archive.extractall(self.package, filter='data')
        shutil.copytree(self.package / 'payload', self.root)
        for path, data in [('tmp/sysinfo/board_name', 'thwc,ufi001c'),
                           ('etc/ufi001c-release', 'UFI001C v3.1.1 Routing / OpenWrt 25.12.5\n'),
                           ('etc/config/podkop', json.dumps({'settings':'settings'})),
                           ('etc/config/sing-box', json.dumps({'main':'sing-box', 'main.failsafe':'0'})),
                           ('etc/config/dhcp', '{}')]:
            p = self.root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data)
        (self.root / 'root').mkdir()
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        FIXTURE_ROOT=str(self.root))
        self.executable(self.bin / 'id', '#!/bin/sh\necho 0\n')
        self.executable(self.bin / 'pidof', '#!/bin/sh\nexit 1\n')
        self.executable(self.bin / 'ufi-resolve', '#!/bin/sh\nexit 1\n')
        self.executable(self.bin / 'uci', '#!' + sys.executable + '\n' + '''import json, os, pathlib, sys
r=pathlib.Path(os.environ['FIXTURE_ROOT']); a=[v for v in sys.argv[1:] if v != '-q']
pending=r/'pending.json'; d=json.loads(pending.read_text()) if pending.exists() else {}
def load(name):
 p=r/'etc/config'/name
 return json.loads(p.read_text()) if p.exists() else {}
cmd=a[0]
if cmd in ('changes','batch'): sys.exit(0)
if cmd in ('commit','revert'):
 name=a[1]
 if cmd=='commit':
  if os.environ.get('FAIL_COMMIT')=='1' and name=='sing-box' and not (r/'failed-once').exists():
   (r/'failed-once').touch(); sys.exit(1)
  value=load(name); value.update(d.pop(name,{})); (r/'etc/config'/name).write_text(json.dumps(value))
 else: d.pop(name,None)
elif cmd=='show':
 name=a[1]; value=load(name); value.update(d.get(name,{}))
 for k,v in value.items(): print(name+'.'+k+'='+str(v))
elif cmd=='get':
 name,key=a[1].split('.',1); value=load(name); value.update(d.get(name,{}))
 if key not in value: sys.exit(1)
 print(value[key])
elif cmd in ('set','add_list'):
 pair,val=a[1].split('=',1); name,key=pair.split('.',1); d.setdefault(name,{})[key]=val
else: raise AssertionError(a)
pending.write_text(json.dumps(d))
''')
        for service in ('podkop', 'podkop-engine', 'dnsmasq'):
            p = self.root / 'etc/init.d' / service
            self.executable(p, '#!/bin/sh\n[ "$1" != enabled ] || exit 1\nexit 0\n')

    def executable(self, path, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        path.chmod(0o755)

    def run_fix(self):
        script = (PORT / 'hotfix/install.sh').read_text()
        script = script.replace('sh /usr/lib/podkop/ufi_startup.sh resolve', 'ufi-resolve')
        for prefix in ('/etc/', '/root/', '/tmp/sysinfo/'):
            script = script.replace(prefix, str(self.root) + prefix)
        script = script.replace('"/$path', '"' + str(self.root) + '/$path')
        script = script.replace('"/$(dirname', '"' + str(self.root) + '/$(dirname')
        # Relocated test script has its own checksum; production manifest stays unchanged.
        script = script.replace('sha256sum -c SHA256SUMS >/dev/null', 'sha256sum -c SHA256SUMS >/dev/null')
        local = self.package / 'test-install.sh'
        local.write_text(script)
        return subprocess.run(['sh', str(local)], env=self.env, text=True, capture_output=True)

    def test_recreates_empty_form_and_keeps_existing_config_fields(self):
        config = self.root / 'etc/config/podkop'
        config.write_text(json.dumps({'settings':'settings', 'settings.example':'keep-me'}))
        p = self.run_fix()
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        data = json.loads(config.read_text())
        self.assertEqual(data['settings.example'], 'keep-me')
        self.assertEqual(data['main'], 'section')
        self.assertEqual(data['settings.ufi_dns_proxy_section'], '@auto')
        self.assertIn('INSTALLED', p.stdout)
        self.assertTrue((self.root / 'root/ufi-setup-last-backup').is_file())

    def test_preserves_existing_proxy_url_and_section_name(self):
        config = self.root / 'etc/config/podkop'
        config.write_text(json.dumps({'settings':'settings', 'custom':'section',
                                     'custom.proxy_string':'private-test-value'}))
        p = self.run_fix()
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        data = json.loads(config.read_text())
        self.assertEqual(data['custom.proxy_string'], 'private-test-value')
        self.assertNotIn('main', data)
        self.assertNotIn('private-test-value', p.stdout + p.stderr)

    def test_unknown_installed_program_stops_before_config_or_file_changes(self):
        program = self.root / 'usr/bin/podkop'
        program.write_text('unknown local edit\n')
        saved = (self.root / 'etc/config/podkop').read_bytes()
        p = self.run_fix()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Modified/unknown', p.stderr)
        self.assertEqual(program.read_text(), 'unknown local edit\n')
        self.assertEqual((self.root / 'etc/config/podkop').read_bytes(), saved)
        self.assertFalse(list((self.root / 'root').glob('ufi-setup-backup.*')))

    def test_commit_failure_rolls_back_configuration(self):
        saved = {name: (self.root / 'etc/config' / name).read_bytes() for name in ('podkop','sing-box','dhcp')}
        self.env['FAIL_COMMIT'] = '1'
        p = self.run_fix()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('restoring', p.stderr)
        for name, content in saved.items():
            self.assertEqual((self.root / 'etc/config' / name).read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
