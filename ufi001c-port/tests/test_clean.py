"""Exercise the actual clean generator and forbid the old recovery services."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import urlencode

PORT = Path(__file__).resolve().parents[1]
CLEAN = PORT / 'clean'
LIB = CLEAN / 'package/openstick/podkop/files/usr/lib'
PROGRAM = (LIB.parent / 'bin/podkop').read_text()
FUNCTIONS = PROGRAM[PROGRAM.index('check_requirements() {'):PROGRAM.rindex('\ncase "$1" in')]
spec = importlib.util.spec_from_file_location('prepare_clean', PORT / 'prepare_clean.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class CleanTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.tmp = Path(self.work.name)

    def shell(self, command, protocol='tcp', server='77.88.8.8', dns_answer='ok'):
        lib = self.tmp / 'lib'
        lib.mkdir(exist_ok=True)
        for source in LIB.iterdir():
            (lib / source.name).write_text(source.read_text().replace('/usr/lib/podkop', str(lib)))
        functions = self.tmp / 'functions.sh'
        functions.write_text(FUNCTIONS)
        extra = {'uplinkDataPlacement': 'header', 'uplinkDataKey': 'x-edge-data', 'uplinkChunkSize': 4000}
        link = 'vless://00000000-0000-4000-8000-000000000001@proxy.example:443?' + urlencode({
            'type': 'xhttp', 'security': 'tls', 'sni': 'proxy.example', 'host': 'proxy.example',
            'path': '/test', 'mode': 'packet-up', 'extra': json.dumps(extra)})
        engine = os.environ.get('UFI_TEST_ENGINE')
        engine_fn = ('sing-box() { ' + ' '.join(shlex.quote(v) for v in shlex.split(engine)) + ' "$@"; }') if engine else '''sing-box() {
case "$1" in
version) printf 'sing-box version 1.13.21-pdk-r12\nFeatures: transport.xhttp,tools.decode-link\n' ;;
tools) printf '%s\n' '{"type":"vless","server":"proxy.example","server_port":443,"uuid":"00000000-0000-4000-8000-000000000001","tls":{"enabled":true,"server_name":"proxy.example"},"transport":{"type":"xhttp","mode":"packet-up","path":"/test","uplink_data_placement":"header","uplink_data_key":"x-edge-data","uplink_chunk_size":4000}}' ;;
esac
}'''
        script = '\n'.join([
            '. ' + shlex.quote(str(PORT / 'tests/fixtures/openwrt-functions.sh')),
            *['. ' + shlex.quote(str(lib / name)) for name in
              ('constants.sh', 'helpers.sh', 'sing_box_config_manager.sh', 'sing_box_config_facade.sh', 'rulesets.sh')],
            '. ' + shlex.quote(str(functions)),
            'log() { printf "%s\\n" "$1" >&2; }',
            'uci() { return 1; }', engine_fn,
            'network_get_ipaddr() { eval "$1=192.168.1.1"; }',
            'dig() { printf "%s\\n" "$*" >&2; ' + {
                'ok': "printf ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\\nexample.com. 60 IN A 93.184.215.14\\n'",
                'servfail': "printf ';; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 1\\n'",
                'empty': "printf ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\\n'",
            }[dns_answer] + '; }',
            'config settings settings', 'option dns_type ' + shlex.quote(protocol),
            'option dns_server ' + shlex.quote(server), "option bootstrap_dns_server 77.88.8.8",
            'option log_level warn', 'option dns_rewrite_ttl 60', 'option enable_yacd 0',
            'option cache_path /tmp/sing-box/cache.db',
            'config section main', 'option connection_type proxy', 'option proxy_config_type url',
            'option proxy_string ' + shlex.quote(link), command,
        ])
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=90)

    def generate(self, **kwargs):
        p = self.shell('sing_box_save_config() { printf "%s\\n" "$config"; }; sing_box_init_config', **kwargs)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_upstream_snapshot_and_declared_dns_patch_match_manifest(self):
        prepare.verify_source()
        manifest = json.loads((CLEAN / 'podkop-upstream.json').read_text())
        self.assertEqual(len(manifest['files_sha256']), 26)
        self.assertEqual(len(manifest['modified_sha256']), 7)

    def test_prepare_excludes_legacy_services_and_installs_upstream_ui(self):
        shutil.copytree(PORT / 'overlay', self.tmp, dirs_exist_ok=True)
        prepare.apply(self.tmp)
        for name in prepare.REMOVED_PACKAGES:
            self.assertFalse((self.tmp / 'package/openstick' / name).exists())
        self.assertFalse((self.tmp / 'package/openstick/podkop/files/etc/init.d/ufi-dns-health').exists())
        self.assertFalse(list((self.tmp / 'package/openstick/podkop/files/usr/lib').glob('ufi_*')))
        engine = (self.tmp / 'package/openstick/podkop-engine/Makefile').read_text()
        self.assertNotIn('$(INSTALL_BIN) ./files/failsafe', engine)
        self.assertNotIn('$(INSTALL_BIN) ./files/podkop-engine.init', engine)
        for path in ('podkop/files/usr/bin/podkop', 'luci-app-podkop/htdocs/luci-static/resources/view/podkop/section.js'):
            self.assertEqual((self.tmp / 'package/openstick' / path).read_bytes(), (CLEAN / 'package/openstick' / path).read_bytes())

    def test_real_generator_keeps_dns_direct_with_tcp_and_fakeip_cache(self):
        data = self.generate()
        servers = {s['tag']: s for s in data['dns']['servers']}
        for tag in ('dns-server', 'bootstrap-dns-server'):
            self.assertEqual(servers[tag]['type'], 'tcp')
            self.assertEqual(servers[tag]['server'], '77.88.8.8')
            self.assertNotIn('detour', servers[tag])
        self.assertEqual(servers['fakeip-server']['inet4_range'], '198.18.0.0/15')
        self.assertTrue(data['experimental']['cache_file']['store_fakeip'])
        transport = next(o['transport'] for o in data['outbounds'] if o['type'] == 'vless')
        self.assertEqual(transport['uplink_data_key'], 'x-edge-data')
        self.assertEqual(transport['uplink_chunk_size'], 4000)
        self.assertEqual(transport['uplink_data_placement'], 'header')
        if os.environ.get('UFI_TEST_ENGINE'):
            config = self.tmp / 'config.json'
            config.write_text(json.dumps(data))
            p = subprocess.run(shlex.split(os.environ['UFI_TEST_ENGINE']) + ['check', '-c', str(config)], capture_output=True, text=True, timeout=90)
            self.assertEqual(p.returncode, 0, p.stderr)

    def test_hostname_doh_uses_independent_tcp_bootstrap(self):
        data = self.generate(protocol='doh', server='dns.example/dns-query')
        servers = {s['tag']: s for s in data['dns']['servers']}
        self.assertEqual(servers['dns-server']['type'], 'https')
        self.assertEqual(servers['dns-server']['domain_resolver'], 'bootstrap-dns-server')
        self.assertEqual(servers['bootstrap-dns-server']['type'], 'tcp')
        self.assertNotIn('detour', servers['dns-server'])

    def test_dns_diagnostics_reject_servfail_and_empty_successful_dig(self):
        for answer in ('ok', 'servfail', 'empty'):
            with self.subTest(answer=answer):
                p = self.shell('dns_query_ok @77.88.8.8 example.com A +tcp', dns_answer=answer)
                self.assertEqual(p.returncode == 0, answer == 'ok', p.stderr)

    def test_rootfs_gate_rejects_a_residual_network_guard(self):
        spec = importlib.util.spec_from_file_location('clean_gate', PORT / 'check_rootfs.py')
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        for installed, source in gate.FILES.items():
            p = self.tmp / installed
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(gate.source_bytes(source))
        bad = self.tmp / 'etc/init.d/ufi-care'
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text('# old network reset daemon\n')
        with self.assertRaisesRegex(ValueError, 'Unwanted'):
            gate.verify(self.tmp)
