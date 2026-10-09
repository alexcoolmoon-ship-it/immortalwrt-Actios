"""Deleted/new/empty-section regressions from the October 9 device report.

Runs real OpenWrt config functions and the actual Podkop generator; no networking.
The functions fixture is unmodified OpenWrt 25.12.5 package/base-files/files/lib/functions.sh.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

PORT = Path(__file__).resolve().parents[1]
LIB = PORT / 'overlay/package/openstick/podkop/files/usr/lib'
UI = PORT / 'overlay/package/openstick/luci-app-podkop/htdocs/luci-static/resources/view/podkop'
FIXTURE = PORT / 'tests/fixtures/openwrt-functions.sh'
PROGRAM = (LIB.parent / 'bin/podkop').read_text()
# Drop only the device-specific imports and CLI dispatch. Test production functions.
FUNCTIONS = PROGRAM[PROGRAM.index('check_requirements() {'):PROGRAM.rindex('\ncase "$1" in')]
URL = 'vless://00000000-0000-4000-8000-000000000001@proxy.example:443?security=tls&sni=proxy.example&type=xhttp&path=%2Ftest&mode=packet-up'


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)

    def run_shell(self, config, command):
        local_lib = self.tmp / 'lib'
        local_lib.mkdir(exist_ok=True)
        for source in LIB.iterdir():
            if source.is_file():
                (local_lib / source.name).write_text(source.read_text().replace('/usr/lib/podkop', str(local_lib)))
        functions = FUNCTIONS.replace('/usr/lib/podkop', str(local_lib))
        (self.tmp / 'functions.sh').write_text(functions)
        engine = os.environ.get('UFI_TEST_ENGINE')
        engine_fn = ('sing-box() { ' + ' '.join(shlex.quote(v) for v in shlex.split(engine)) + ' "$@"; }') if engine else '''sing-box() {
case "$1" in
version) printf 'sing-box version 1.13.21-pdk-r12\nFeatures: transport.xhttp,tools.decode-link\n' ;;
tools) printf '%s\n' '{"type":"vless","server":"proxy.example","server_port":443,"uuid":"00000000-0000-4000-8000-000000000001","tls":{"enabled":true,"server_name":"proxy.example"},"transport":{"type":"xhttp","mode":"packet-up","path":"/test"}}' ;;
esac
}'''
        script = '\n'.join([
            '. ' + shlex.quote(str(FIXTURE)),
            '. ' + shlex.quote(str(local_lib / 'ufi_startup.sh')),
            *['. ' + shlex.quote(str(local_lib / name)) for name in
              ('constants.sh', 'helpers.sh', 'sing_box_config_manager.sh', 'sing_box_config_facade.sh', 'rulesets.sh')],
            '. ' + shlex.quote(str(self.tmp / 'functions.sh')),
            'log() { printf "%s\\n" "$1" >&2; }',
            'uci() { return 1; }',  # The runtime selection must not mutate persistent UCI.
            engine_fn,
            'network_get_ipaddr() { eval "$1=192.168.1.1"; }',
            'config settings settings',
            "option ufi_dns_proxy_section '@auto'",
            "option download_lists_via_proxy 1",
            "option download_lists_via_proxy_section '@auto'",
            "option dns_type doh",
            "option dns_server 1.1.1.1",
            "option bootstrap_dns_server 77.88.8.8",
            "option log_level warn",
            "option dns_rewrite_ttl 60",
            "option enable_yacd 0",
            "option cache_path /tmp/sing-box/cache.db",
            config,
            command,
        ])
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True)

    def proxy(self, name, payload=URL):
        return '\n'.join(['config section ' + shlex.quote(name),
                          'option connection_type proxy', 'option proxy_config_type url',
                          'option proxy_string ' + shlex.quote(payload)])

    def test_deleted_named_section_resolves_new_name(self):
        p = self.run_shell(self.proxy('new_proxy'), '''config_set settings ufi_dns_proxy_section dns_only
config_set settings download_lists_via_proxy_section dns_only
ufi_resolve_dns_section''')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), 'new_proxy')

    def test_empty_template_does_not_win_over_new_filled_section(self):
        cfg = self.proxy('dns_only', '') + '\n' + self.proxy('new_proxy')
        p = self.run_shell(cfg, 'ufi_prepare_runtime && config_get settings ufi_dns_proxy_section')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), 'new_proxy')

    def test_empty_sections_are_ignored_by_all_runtime_section_loops(self):
        cfg = self.proxy('empty', '   ') + '\n' + self.proxy('new_proxy')
        p = self.run_shell(cfg, 'show() { echo "$1"; }; ufi_each_section show')
        self.assertEqual(p.stdout.strip(), 'new_proxy', p.stderr)
        self.assertNotIn('config_foreach configure_', PROGRAM)
        self.assertNotIn('config_foreach import_', PROGRAM)

    def test_no_sections_keeps_service_stopped_and_restores_normal_dns(self):
        p = self.run_shell('', '''stop() { echo dns-restored; }
start_main() { echo MUST-NOT-START; return 1; }
start''')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), 'dns-restored')
        self.assertIn('No configured', p.stderr)

    def test_all_empty_sections_behave_like_fresh_firmware(self):
        p = self.run_shell(self.proxy('dns_only', ''), '''stop() { echo dns-restored; }
start_main() { echo MUST-NOT-START; return 1; }
start''')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), 'dns-restored')

    def test_explicit_valid_proxy_choice_is_preserved(self):
        cfg = self.proxy('first') + '\n' + self.proxy('second')
        p = self.run_shell(cfg, 'ufi_resolve_dns_section second')
        self.assertEqual(p.stdout.strip(), 'second', p.stderr)

    def test_selector_is_not_silently_used_as_single_dns_proxy(self):
        cfg = "config section chooser\noption connection_type proxy\noption proxy_config_type selector\nlist selector_proxy_links " + shlex.quote(URL)
        p = self.run_shell(cfg, 'ufi_prepare_runtime')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('single-URL', p.stderr)

    def test_reload_restores_dhcp_before_starting_again(self):
        p = self.run_shell('', 'stop() { echo restore; }; start() { echo start; }; reload')
        self.assertEqual(p.stdout.splitlines(), ['restore', 'start'])

    def test_real_generator_with_empty_template_and_arbitrary_proxy_name(self):
        cfg = self.proxy('dns_only', '') + '\n' + self.proxy('proxy_486')
        p = self.run_shell(cfg, '''ufi_prepare_runtime || exit 1
sing_box_save_config() { local output; output=$(mktemp); sing_box_cm_save_config_to_file "$config" "$output"; cat "$output"; rm "$output"; }
sing_box_init_config''')
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertNotIn('dns_only-out', {o['tag'] for o in data['outbounds']})
        proxy = next(o for o in data['outbounds'] if o['tag'] == 'proxy_486-out')
        self.assertEqual(proxy['transport']['type'], 'xhttp')
        self.assertEqual(next(s for s in data['dns']['servers'] if s['tag'] == 'dns-server')['detour'], 'proxy_486-out')
        self.assertTrue(any(r.get('outbound') == 'proxy_486-out' and 'service-mixed-in' in r.get('inbound', []) for r in data['route']['rules']))
        engine = os.environ.get('UFI_TEST_ENGINE')
        if engine:
            path = self.tmp / 'generated.json'
            path.write_text(json.dumps(data))
            check = subprocess.run(shlex.split(engine) + ['check', '-c', str(path)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_luci_empty_deleted_and_new_sections_have_valid_automatic_choice(self):
        # Execute the shipped JS, using the documented Map.data interface.
        code = '''const assert = require('assert');
const fs = require('fs');
const mod = new Function('baseclass', '_', fs.readFileSync(process.argv[1], 'utf8'))({extend: x => x}, x => x);
let sections = []; let values = {download_lists_via_proxy_section: 'dns_only'};
const data = { sections: () => sections, get: (c,s,k) => values[k], set: (c,s,k,v) => {values[k]=v;} };
const o = {option: 'download_lists_via_proxy_section', map: {data}};
mod.configure(o, false);
(async () => {
 await o.load();
 assert.deepStrictEqual(o.keylist, ['@auto']);
 assert.equal(o.cfgvalue('settings'), '@auto');
 o.write('settings', 'dns_only'); assert.equal(values[o.option], '@auto');
 sections = [{'.name':'486', '.type':'section'}];
 await o.load(); assert(o.keylist.includes('486'));
 o.write('settings', '486'); assert.equal(values[o.option], '486');
 sections = [];
 o.write('settings', '486'); assert.equal(values[o.option], '@auto');
 await o.load(); assert.deepStrictEqual(o.keylist, ['@auto']);
 console.log('empty, add, delete: PASS');
})().catch(e => {console.error(e); process.exit(1);});'''
        p = subprocess.run(['node', '-e', code, str(UI / 'ufi_sections.js')], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('PASS', p.stdout)


if __name__ == '__main__':
    unittest.main()
