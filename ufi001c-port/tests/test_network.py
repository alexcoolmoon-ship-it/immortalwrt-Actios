"""Regressions from the UFI001C device reports. No host network/flash writes."""
import copy
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PORT = Path(__file__).resolve().parents[1]
LIB = PORT / 'overlay/package/openstick/podkop/files/usr/lib'
FLAGS = ('IP_ADVANCED_ROUTER', 'IP_MULTIPLE_TABLES', 'FIB_RULES')
BASE = {
    'dns': {'servers': [
        {'tag': 'dns-server', 'type': 'https', 'server': '1.1.1.1'},
        {'tag': 'bootstrap-dns-server', 'type': 'udp', 'server': '77.88.8.8'}]},
    'outbounds': [{'type': 'vless', 'tag': 'dns_only-out',
                   'server': 'proxy.example', 'server_port': 443,
                   'uuid': '00000000-0000-4000-8000-000000000001',
                   'transport': {'type': 'xhttp', 'mode': 'packet-up', 'path': '/test'}}],
    'inbounds': [{'type': 'mixed', 'tag': 'service-mixed-in',
                  'listen': '127.0.0.1', 'listen_port': 4534}],
    'route': {'rules': [{'inbound': ['service-mixed-in'], 'outbound': 'dns_only-out'}],
              'rule_set': [{'type': 'remote', 'tag': 'test', 'url': 'https://example.com/test.srs'}]}}


def transform(config):
    return subprocess.run(['sh', str(LIB / 'ufi_dns_tunnel.sh'), 'filter', 'dns_only'],
                          input=json.dumps(config), text=True, capture_output=True)


class NetworkRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.tmp) + os.pathsep + self.env['PATH'],
                        TEST_STATE=str(self.tmp / 'state.json'), TEST_MODE='ok',
                        TEST_CONFIG=str(self.tmp / 'config.json'))

    def executable(self, name, source):
        p = self.tmp / name
        p.write_text('#!' + sys.executable + '\n' + source)
        p.chmod(0o755)

    def routing_fixture(self, flags=FLAGS):
        config = self.tmp / 'config.gz'
        with gzip.open(config, 'wt') as out:
            out.write(''.join('CONFIG_' + flag + '=y\n' for flag in flags))
        # Relocate only /proc/config.gz; command behaviour is simulated below.
        script = self.tmp / 'routing.sh'
        script.write_text((LIB / 'ufi_policy_routing.sh').read_text().replace('/proc/config.gz', str(config)))
        self.executable('ip', '''import json, os, pathlib, sys
p = pathlib.Path(os.environ['TEST_STATE'])
s = json.loads(p.read_text()) if p.exists() else {'rule': False, 'route': False, 'calls': []}
a = sys.argv[1:]; mode = os.environ['TEST_MODE']; s['calls'].append(a)
rc = 0
if a == ['-4', 'rule', 'show']:
    if mode != 'empty':
        print('0: from all lookup local')
        if s['rule']: print('105: from all fwmark 0x100000/0x100000 lookup podkop')
        print('32766: from all lookup main')
elif a[1:3] == ['route', 'replace']:
    s['route'] = True
elif a[1:3] == ['rule', 'add']:
    if mode == 'rule_error': rc = 2
    elif mode != 'silent_rule': s['rule'] = True
elif a == ['-4', 'route', 'show', 'table', '105']:
    if s['route']: print('local default dev lo scope host')
elif a[:3] == ['-4', 'route', 'get']:
    if mode == 'wrong_lookup': print('198.18.0.254 via 10.0.0.1 dev wwan0')
    else: print('local 198.18.0.254 dev lo table podkop src 192.168.1.1 mark 0x100000')
else: raise AssertionError(a)
p.write_text(json.dumps(s))
sys.exit(rc)
''')
        return script

    def run_routing(self, script, mode='ok'):
        self.env['TEST_MODE'] = mode
        return subprocess.run(['sh', str(script), 'ensure'], env=self.env, text=True, capture_output=True)

    def test_old_kernel_is_rejected_before_any_route_write(self):
        p = self.run_routing(self.routing_fixture(('FIB_RULES',)))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('IP_ADVANCED_ROUTER', p.stderr)
        self.assertFalse((self.tmp / 'state.json').exists())

    def test_empty_successful_rule_dump_is_not_kernel_support(self):
        p = self.run_routing(self.routing_fixture(), 'empty')
        self.assertNotEqual(p.returncode, 0)
        state = json.loads((self.tmp / 'state.json').read_text())
        self.assertEqual(state['calls'], [['-4', 'rule', 'show']])

    def test_routing_restart_is_idempotent(self):
        script = self.routing_fixture()
        for _ in range(2):
            p = self.run_routing(script)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('rule and local route installed', p.stdout)
        calls = json.loads((self.tmp / 'state.json').read_text())['calls']
        self.assertEqual(sum(a[1:3] == ['rule', 'add'] for a in calls), 1)

    def test_failed_or_ineffective_route_setup_cannot_pass(self):
        script = self.routing_fixture()
        for mode in ('rule_error', 'silent_rule', 'wrong_lookup'):
            with self.subTest(mode=mode):
                (self.tmp / 'state.json').unlink(missing_ok=True)
                p = self.run_routing(script, mode)
                self.assertNotEqual(p.returncode, 0, p.stdout)

    def test_build_gate_requires_all_actual_kernel_flags(self):
        config = self.tmp / 'build_dir/target-aarch64/linux-msm89xx_msm8916/linux-6.12.94/.config'
        config.parent.mkdir(parents=True)
        for flags, valid in [(('FIB_RULES',), False), (FLAGS, True)]:
            config.write_text(''.join('CONFIG_' + flag + '=y\n' for flag in flags))
            p = subprocess.run([sys.executable, str(PORT / 'check_kernel.py'), str(self.tmp)],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode == 0, valid, p.stderr)

    def test_dns_filter_preserves_xhttp_and_survives_regeneration(self):
        p = transform(BASE)
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data['dns']['servers'][0]['detour'], 'dns_only-out')
        self.assertNotIn('detour', data['dns']['servers'][1])
        self.assertEqual(data['outbounds'][0]['domain_resolver']['server'], 'bootstrap-dns-server')
        self.assertEqual(data['outbounds'][0]['transport'], BASE['outbounds'][0]['transport'])
        self.assertEqual(data['outbounds'][0]['uuid'], BASE['outbounds'][0]['uuid'])
        self.assertEqual(data['route']['rule_set'][0]['download_detour'], 'dns_only-out')
        self.assertEqual(json.loads(transform(data).stdout), data)

    def test_dns_filter_rejects_missing_direct_and_chained_outbound(self):
        for mode in ('missing', 'direct', 'chained'):
            with self.subTest(mode=mode):
                data = copy.deepcopy(BASE)
                if mode == 'missing': data['outbounds'] = []
                elif mode == 'direct': data['outbounds'][0]['type'] = 'direct'
                else: data['outbounds'][0]['detour'] = 'another-proxy'
                self.assertNotEqual(transform(data).returncode, 0)

    def dns_probe_fixture(self):
        data = json.loads(transform(BASE).stdout)
        (self.tmp / 'config.json').write_text(json.dumps(data))
        self.executable('uci', '''import os, sys
key = sys.argv[-1]
if key == 'podkop.settings.config_path': print(os.environ['TEST_CONFIG'])
elif key == 'podkop.settings.ufi_dns_proxy_section': print('dns_only')
else: sys.exit(1)
''')
        self.executable('curl', '''import os, pathlib, sys
a = sys.argv[1:]
assert a[a.index('--proxy') + 1] == 'http://127.0.0.1:4534'
assert a[a.index('--noproxy') + 1] == ''
assert a[-1] == 'https://1.1.1.1:443/dns-query'
query = pathlib.Path(a[a.index('--data-binary') + 1][1:]).read_bytes()
assert query == bytes.fromhex('000001000001000000000000076578616d706c6503636f6d0000010001')
mode = os.environ['TEST_MODE']
if mode == 'http_error': sys.exit(22)
answer = bytes.fromhex('000081800001000100000000') + query[12:] + bytes.fromhex('c00c000100010000003c0004cb007109')
if mode == 'servfail': answer = answer[:3] + bytes([0x82]) + answer[4:]
if mode == 'empty_answer': answer = answer[:6] + bytes(2) + answer[8:]
if mode == 'truncated': answer = answer[:7]
sys.stdout.buffer.write(answer)
''')
        return data

    def test_dns_probe_uses_proxy_and_validates_wire_response(self):
        self.dns_probe_fixture()
        for mode in ('ok', 'http_error', 'servfail', 'empty_answer', 'truncated'):
            with self.subTest(mode=mode):
                self.env['TEST_MODE'] = mode
                p = subprocess.run(['sh', str(LIB / 'ufi_dns_tunnel.sh'), 'probe-dns'],
                                   env=self.env, text=True, capture_output=True)
                self.assertEqual(p.returncode == 0, mode == 'ok', p.stderr)

    def test_dns_probe_rejects_mismatched_running_configuration(self):
        data = self.dns_probe_fixture()
        data['route']['rules'][0]['outbound'] = 'different-out'
        (self.tmp / 'config.json').write_text(json.dumps(data))
        p = subprocess.run(['sh', str(LIB / 'ufi_dns_tunnel.sh'), 'probe-dns'],
                           env=self.env, text=True, capture_output=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('do not match', p.stderr)

    def test_dns_rollback_restores_engine_policy_as_well_as_podkop(self):
        # Restore real files in a disposable root; service/uci calls are stubs.
        for relative in ('root', 'etc/config', 'etc/init.d', 'usr/bin'):
            (self.tmp / relative).mkdir(parents=True, exist_ok=True)
        backup = self.tmp / 'root/ufi-dns-tunnel-backup.test'
        backup.mkdir()
        originals = {'podkop.program': '#!/bin/sh\n# saved program\n',
                     'podkop.config': 'config settings settings\n',
                     'sing-box.config': "config sing-box main\n option dns_fallback '1'\n option failsafe '1'\n"}
        for name, text in originals.items():
            (backup / name).write_text(text)
        (self.tmp / 'root/ufi-dns-tunnel-last-backup').write_text(str(backup))
        (self.tmp / 'etc/config/sing-box').write_text("config sing-box main\n option dns_fallback '0'\n")
        self.executable('uci', 'import sys\nsys.exit(0)\n')
        for service in ('podkop-engine', 'podkop'):
            self.executable('etc/init.d/' + service, '''import pathlib, sys
root = pathlib.Path(__file__).resolve().parents[2]
assert sys.argv[1:] == ['restart']
assert "option dns_fallback '1'" in (root / 'etc/config/sing-box').read_text()
with (root / 'services.log').open('a') as out: out.write(pathlib.Path(__file__).name + '\\n')
''')
        script = (LIB / 'ufi_dns_tunnel.sh').read_text()
        for prefix in ('/root/', '/etc/', '/usr/bin/'):
            script = script.replace(prefix, str(self.tmp) + prefix)
        local_script = self.tmp / 'rollback.sh'
        local_script.write_text(script)
        p = subprocess.run(['sh', str(local_script), 'rollback'],
                           env=self.env, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        for saved, installed in (('podkop.program', 'usr/bin/podkop'),
                                 ('podkop.config', 'etc/config/podkop'),
                                 ('sing-box.config', 'etc/config/sing-box')):
            self.assertEqual((self.tmp / installed).read_text(), originals[saved])
        self.assertEqual((self.tmp / 'services.log').read_text().splitlines(), ['podkop-engine', 'podkop'])

    def test_rootfs_gate_rejects_stale_helper_and_wrong_architecture(self):
        spec = importlib.util.spec_from_file_location('ufi_rootfs_gate', PORT / 'check_rootfs.py')
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        for installed, source in gate.FILES.items():
            p = self.tmp / installed
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes((PORT / source).read_bytes())
        header = bytearray(64)
        header[:5] = b'\x7fELF\x02'
        header[18:20] = b'\xb7\x00'
        for name in ('ip-full', 'od-coreutils'):
            p = self.tmp / 'usr/libexec' / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(header)
        self.assertEqual(gate.verify(self.tmp)['status'], 'passed')
        helper = self.tmp / 'usr/lib/podkop/ufi_policy_routing.sh'
        saved = helper.read_bytes()
        helper.write_text('# obsolete helper\n')
        with self.assertRaisesRegex(ValueError, 'stale'):
            gate.verify(self.tmp)
        helper.write_bytes(saved)
        header[18:20] = b'\x3e\x00'  # x86-64 is not the target modem architecture.
        (self.tmp / 'usr/libexec/ip-full').write_bytes(header)
        with self.assertRaisesRegex(ValueError, 'AArch64'):
            gate.verify(self.tmp)


if __name__ == '__main__':
    unittest.main()
