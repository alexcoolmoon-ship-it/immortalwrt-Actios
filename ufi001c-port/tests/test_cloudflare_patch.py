"""Cloudflare tunnel, independent proxy bootstrap and offline patch packaging."""
import concurrent.futures
import hashlib
import http.server
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import select
import shlex
import signal
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
import test_clean as generator
import test_clean_live as live

PORT = Path(__file__).resolve().parents[1]
FIX = PORT / 'cloudflare-fix'
spec = importlib.util.spec_from_file_location('cloudflare_build', FIX / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
LEGACY_PROGRAM = build.patched_program()
# Exercise the actual program shipped in the new image, not a patch-only copy.
PROGRAM = generator.PROGRAM
FUNCTIONS = PROGRAM[PROGRAM.index('check_requirements() {'):PROGRAM.rindex('\ncase "$1" in')]


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = generator.CleanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tmp = self.fixture.tmp

    def generate(self, command=''):
        output = self.tmp / 'config.json'
        with patch.object(generator, 'FUNCTIONS', FUNCTIONS):
            p = self.fixture.shell('''
config_set settings cloudflare_dns_patch 1
config_set settings cloudflare_dns_section main
''' + command + '''
sing_box_save_config() { sing_box_cm_save_config_to_file "$config" ''' + shlex.quote(str(output)) + '''; }
sing_box_init_config
''', protocol='doh', server='1.1.1.1')
        return p, json.loads(output.read_text()) if output.exists() else None

    def test_proxy_bootstrap_is_independent_and_cloudflare_is_tunnelled(self):
        p, config = self.generate()
        self.assertEqual(p.returncode, 0, p.stderr)
        servers = {s['tag']: s for s in config['dns']['servers']}
        self.assertEqual(servers['dns-server']['detour'], 'main-out')
        self.assertEqual(servers['dns-server']['server'], '1.1.1.1')
        self.assertEqual(servers['dns-server']['type'], 'https')
        self.assertEqual(servers['dns-server']['tls']['server_name'], 'cloudflare-dns.com')
        self.assertEqual(servers['bootstrap-dns-server'], {
            'type':'tcp','tag':'bootstrap-dns-server','server':'77.88.8.8','server_port':53})
        outbound = next(o for o in config['outbounds'] if o['tag'] == 'main-out')
        self.assertEqual(outbound['domain_resolver'], {'server':'bootstrap-dns-server','strategy':'ipv4_only'})
        self.assertEqual(str(outbound['transport']['uplink_chunk_size']), '4000')
        self.assertEqual(outbound['transport']['uplink_data_key'], 'x-edge-data')
        self.assertTrue(config['experimental']['cache_file']['store_fakeip'])
        self.assertEqual(servers['fakeip-server']['inet4_range'], '198.18.0.0/15')
        rule = config['route']['rules'][0]
        self.assertEqual(rule['inbound'], ['service-mixed-in'])
        self.assertEqual(rule['outbound'], 'main-out')
        if os.environ.get('UFI_TEST_ENGINE'):
            path = self.tmp / 'checked.json'
            path.write_text(json.dumps(config))
            p = subprocess.run(shlex.split(os.environ['UFI_TEST_ENGINE']) + ['check','-c',str(path)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)

    def test_selector_with_direct_fallback_is_rejected(self):
        p, config = self.generate('''
configure_outbound_handler() {
 config=$(printf '%s' "$config" | jq '.outbounds += [{type:"selector",tag:"main-out",outbounds:["direct-out"]}]')
}
''')
        self.assertNotEqual(p.returncode, 0)
        self.assertIsNone(config)

    def test_existing_service_inbound_is_not_duplicated(self):
        p, config = self.generate('config_set settings download_lists_via_proxy 1\nconfig_set settings download_lists_via_proxy_section main')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(sum(i['tag']=='service-mixed-in' for i in config['inbounds']), 1)
        self.assertEqual(sum('service-mixed-in' in r.get('inbound',[]) for r in config['route']['rules']), 1)

    def test_auto_selection_prefers_dns_only_and_skips_empty_template(self):
        with patch.object(generator, 'FUNCTIONS', FUNCTIONS):
            p = self.fixture.shell('''
config_set main proxy_string ''
config section dns_only
option connection_type proxy
option proxy_config_type url
option proxy_string 'vless://test'
cloudflare_proxy_section
''')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), 'dns_only')

    def test_diagnostics_rejects_unrelated_cached_answer(self):
        with patch.object(generator, 'FUNCTIONS', FUNCTIONS):
            p = self.fixture.shell('''
config_set settings cloudflare_dns_patch 1
cloudflare_doh_query_ok() { return 1; }
config_foreach() { :; }; config_load() { :; }
check_dns_available
''', protocol='doh', server='1.1.1.1')
        self.assertEqual(p.returncode, 0, p.stderr)
        report = json.loads(p.stdout)
        self.assertEqual(report['dns_status'], 0)
        self.assertEqual(report['dns_on_router'], 1)
        self.assertIn('(proxy)', report['dns_server'])

    def test_packaged_payload_matches_program_and_is_valid_shell(self):
        output = self.tmp / 'patch.zip'
        build.build(output)
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            installer = archive.read('UFI001C-Cloudflare-Patch/install.sh').decode()
            self.assertIn(hashlib.sha256(build.BASE.read_bytes()).hexdigest(), installer)
            self.assertIn(hashlib.sha256(LEGACY_PROGRAM.encode()).hexdigest(), installer)
            import base64
            encoded = installer.split("<<'UFI_CLOUDFLARE_PROGRAM'\n",1)[1].split('\nUFI_CLOUDFLARE_PROGRAM',1)[0]
            self.assertEqual(base64.b64decode(encoded).decode(), LEGACY_PROGRAM)
            for name in archive.namelist():
                if name.endswith('.sh'):
                    p = subprocess.run(['sh','-n'], input=archive.read(name), capture_output=True)
                    self.assertEqual(p.returncode, 0, p.stderr)
        p = subprocess.run(['sh','-n'], input=PROGRAM, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def run_installer(self, altered=False):
        root = self.tmp/'router'
        for folder in ('usr/bin','etc/config','etc/sing-box','etc/init.d','tmp/sysinfo','root','var/lock','mockbin'):
            (root/folder).mkdir(parents=True,exist_ok=True)
        program = build.BASE.read_bytes() + (b'\n# other patch\n' if altered else b'')
        (root/'usr/bin/podkop').write_bytes(program)
        (root/'etc/config/podkop').write_bytes(b'ORIGINAL PRIVATE CONFIG\n')
        (root/'etc/sing-box/config.json').write_bytes(b'ORIGINAL PRIVATE JSON\n')
        (root/'tmp/sysinfo/board_name').write_text('thwc,ufi001c\n')
        (root/'etc/ufi001c-release').write_text('UFI001C v3.2.1 Clean / OpenWrt 25.12.5\n')
        mock = root/'mockbin'
        def executable(name,text):
            path=mock/name;path.write_text('#!/bin/sh\n'+text);path.chmod(0o755)
        executable('id','echo 0\n')
        executable('uci', '''
if [ "$1" = -q ]; then shift; fi
case "$1" in
 get) echo '''+shlex.quote(str(root/'etc/sing-box/config.json'))+''' ;;
 commit) echo MUTATED_CONFIG > '''+shlex.quote(str(root/'etc/config/podkop'))+''' ;;
esac
exit 0
''')
        executable('sh','''
case "$2" in
 cloudflare_section) echo main ;;
 cloudflare_generate) echo PREFLIGHT_FAILED >&2; exit 1 ;;
 *) exec /bin/sh "$@" ;;
esac
''')
        for name in ('sing-box','curl','dig','hexdump'):
            executable(name,'exit 0\n')
        service=root/'etc/init.d/podkop'
        service.write_text('#!/bin/sh\necho RELOADED > '+shlex.quote(str(root/'service-used'))+'\n')
        service.chmod(0o755)
        output=self.tmp/'install-test.zip';build.build(output)
        with zipfile.ZipFile(output) as archive:
            installer=archive.read('UFI001C-Cloudflare-Patch/install.sh').decode()
        for prefix in ('/usr/bin/podkop','/etc/','/tmp/sysinfo/','/tmp/ufi-cloudflare.','/var/lock/','/root/'):
            installer=installer.replace(prefix,str(root)+prefix)
        path=self.tmp/'install-test.sh';path.write_text(installer)
        result=subprocess.run(['/bin/sh',str(path)],env={**os.environ,'PATH':str(mock)+':'+os.environ['PATH']},capture_output=True,text=True,timeout=10)
        self.assertEqual((root/'usr/bin/podkop').read_bytes(),program)
        self.assertEqual((root/'etc/config/podkop').read_bytes(),b'ORIGINAL PRIVATE CONFIG\n')
        self.assertEqual((root/'etc/sing-box/config.json').read_bytes(),b'ORIGINAL PRIVATE JSON\n')
        self.assertFalse((root/'service-used').exists())
        self.assertFalse((root/'var/lock/ufi-cloudflare.lock').exists())
        return result

    def test_preflight_failure_restores_config_without_reloading_services(self):
        result=self.run_installer()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('LOCAL_ERROR_ROLLED_BACK',result.stdout)

    def test_other_existing_modifications_are_refused_before_any_change(self):
        result=self.run_installer(altered=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('other modifications',result.stderr)

    def test_shipped_defaults_enable_cloudflare_and_keep_empty_proxy_template(self):
        config = generator.CLEAN / 'package/openstick/podkop/files/etc/config/podkop'
        p, data = self.generate('''
config_get test_link main proxy_string
CONFIG_SECTIONS=''
. ''' + shlex.quote(str(config)) + '''
config_get empty main proxy_string
[ -z "$empty" ] || exit 1
config_set main proxy_string "$test_link"
''')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(next(s for s in data['dns']['servers'] if s['tag']=='dns-server')['detour'], 'main-out')

    def test_mode_off_keeps_native_dns_without_service_listener(self):
        p, data = self.generate('config_set settings cloudflare_dns_patch 0')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn('detour', next(s for s in data['dns']['servers'] if s['tag']=='dns-server'))
        self.assertFalse(any(i['tag']=='service-mixed-in' for i in data['inbounds']))

    def test_cloudflare_button_status_does_not_pass_when_disabled_or_probe_fails(self):
        for enabled, probe, expected in ((0,0,0),(1,1,0),(1,0,1)):
            p = self.fixture.shell('''
config_set settings cloudflare_dns_patch ''' + str(enabled) + '''
cloudflare_doh_query_ok() { return ''' + str(probe) + '''; }
cloudflare_status
''')
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout), {'mode':'cloudflare_proxy' if enabled else 'custom', 'dns_status':expected})

    def test_invalid_configuration_stops_before_rules_engine_and_dhcp(self):
        p = self.fixture.shell('''
check_requirements() { :; }; migration() { :; }
process_validate_service() { :; }; sleep() { :; }
br_netfilter_disable() { echo UNEXPECTED_NETFILTER; }
route_table_rule_mark() { echo UNEXPECTED_ROUTE; }
create_nft_rules() { echo UNEXPECTED_NFT; }
sing_box_configure_service() { echo UNEXPECTED_SERVICE; }
sing_box_init_config() { echo CONFIG_REJECTED; return 1; }
dnsmasq_configure() { echo UNEXPECTED_DHCP; }
TMP_SING_BOX_FOLDER=''' + shlex.quote(str(self.tmp/'singbox')) + '''
TMP_RULESET_FOLDER=''' + shlex.quote(str(self.tmp/'rules')) + '''
start
''')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), 'CONFIG_REJECTED')


def question(query):
    end, names = 12, []
    while query[end]:
        n = query[end]
        names.append(query[end+1:end+n+1].decode())
        end += n+1
    return '.'.join(names), query[12:end+5]


class Bootstrap(live.DNSHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            while True:
                query = live.receive(self.request, struct.unpack('!H',live.receive(self.request,2))[0])
                name, q = question(query)
                self.server.names.append(name)
                answer = query[:2] + bytes.fromhex('81800001000100000000') + q
                answer += bytes.fromhex('c00c000100010000003c0004') + socket.inet_aton('127.0.0.1')
                self.request.sendall(struct.pack('!H',len(answer)) + answer)
        except (OSError, EOFError):
            pass


class DoH(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_POST(self):
        query = self.rfile.read(int(self.headers['Content-Length']))
        name, q = question(query)
        self.server.names.append(name)
        if self.server.failing:
            answer = query[:2] + bytes.fromhex('81820001000000000000') + q
        else:
            answer = query[:2] + bytes.fromhex('81800001000100000000') + q
            answer += bytes.fromhex('c00c000100010000003c0004') + socket.inet_aton('203.0.113.7')
        self.send_response(200)
        self.send_header('Content-Type','application/dns-message')
        self.send_header('Content-Length',str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)


class Relay(live.ProxyHandler):
    def handle(self):
        self.request.settimeout(10)
        try:
            _, count = live.receive(self.request,2)
            live.receive(self.request,count)
            self.request.sendall(b'\5\0')
            _, _, _, kind = live.receive(self.request,4)
            if kind == 3:
                target = live.receive(self.request,live.receive(self.request,1)[0]).decode()
            elif kind == 1:
                target = socket.inet_ntoa(live.receive(self.request,4))
            else:
                return
            port = struct.unpack('!H',live.receive(self.request,2))[0]
            self.server.targets.append(target)
            if target == 'proxied.test':
                self.request.sendall(b'\5\0\0\1' + socket.inet_aton('127.0.0.1') + b'\0\0')
                self.request.recv(4096)
                self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
                return
            if (target,port) != ('1.1.1.1',443):
                raise ValueError('Unexpected proxy destination')
            with socket.create_connection(('127.0.0.1',self.server.https_port),timeout=5) as upstream:
                self.request.sendall(b'\5\0\0\1' + socket.inet_aton('127.0.0.1') + b'\0\0')
                while True:
                    ready, _, _ = select.select([self.request,upstream],[],[],10)
                    if not ready:
                        break
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.request else self.request).sendall(data)
        except (OSError, EOFError):
            pass


@unittest.skipUnless(os.environ.get('UFI_TEST_ENGINE'), 'requires actual firmware ARM64 engine')
class CloudflareLiveTests(unittest.TestCase):
    stop_engine = live.LiveCleanTests.stop_engine
    start_engine = live.LiveCleanTests.start_engine
    http_via_fakeip = live.LiveCleanTests.http_via_fakeip
    test_fakeip_reverse_mapping_survives_engine_restart = live.LiveCleanTests.test_fakeip_reverse_mapping_survives_engine_restart

    def setUp(self):
        self.fixture = PatchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tmp = self.fixture.tmp
        cert, key = self.tmp/'cert.pem', self.tmp/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-subj','/CN=cloudflare-dns.com','-addext','subjectAltName=DNS:cloudflare-dns.com,IP:1.1.1.1',
                        '-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
        self.doh = http.server.ThreadingHTTPServer(('127.0.0.1',0),DoH)
        self.doh.daemon_threads = True
        self.doh.names, self.doh.failing = [], False
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert,key)
        context.set_alpn_protocols(['http/1.1'])
        self.doh.socket = context.wrap_socket(self.doh.socket,server_side=True)
        self.dns = live.Server(('127.0.0.1',0),Bootstrap)
        self.dns.names = []
        self.proxy = live.Server(('127.0.0.1',0),Relay)
        self.proxy.https_port, self.proxy.targets = self.doh.server_port, []
        for server in (self.doh,self.dns,self.proxy):
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        rules = self.tmp/'rules'; rules.mkdir()
        p, config = self.fixture.generate('TMP_RULESET_FOLDER=' + shlex.quote(str(rules)) + '''
config_set main user_domain_list_type dynamic
config_set main user_domains proxied.test
''')
        self.assertEqual(p.returncode,0,p.stderr)
        self.cert = cert
        for server in config['dns']['servers']:
            if server['tag'] == 'bootstrap-dns-server':
                server.update(server='127.0.0.1',server_port=self.dns.server_address[1])
            elif server['tag'] == 'dns-server':
                server['tls']['certificate_path'] = str(cert)
        outbound = next(o for o in config['outbounds'] if o['tag']=='main-out')
        outbound.clear()
        outbound.update(type='socks',tag='main-out',server='proxy.test',server_port=self.proxy.server_address[1],version='5',
                        domain_resolver={'server':'bootstrap-dns-server','strategy':'ipv4_only'})
        self.dns_port, self.mixed_port = live.free_port(),live.free_port()
        config['inbounds'] = [
            {'type':'direct','tag':'dns-in','listen':'127.0.0.1','listen_port':self.dns_port},
            {'type':'mixed','tag':'tproxy-in','listen':'127.0.0.1','listen_port':self.mixed_port},
            next(i for i in config['inbounds'] if i['tag']=='service-mixed-in')]
        config['route']['auto_detect_interface'] = False
        config['experimental'].pop('clash_api',None)
        config['experimental']['cache_file']['path'] = str(self.tmp/'cache.db')
        self.config = self.tmp/'runtime.json'
        self.config.write_text(json.dumps(config))
        self.command = shlex.split(os.environ['UFI_TEST_ENGINE'])
        self.process = None
        self.log = (self.tmp/'engine.log').open('w+')
        self.addCleanup(self.log.close)
        self.addCleanup(self.stop_engine)
        self.start_engine()

    def test_doh_load_through_proxy_with_separate_bootstrap(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            answers = list(executor.map(lambda n: live.dns_query(self.dns_port,'load-'+str(n)+'.example'),range(200)))
        self.assertEqual(answers,['203.0.113.7']*200)
        self.assertIn('1.1.1.1',self.proxy.targets)
        self.assertGreaterEqual(len(self.doh.names),201)
        self.assertEqual(set(self.dns.names),{'proxy.test'})

    def test_servfail_does_not_fallback_to_plain_yandex_and_recovers(self):
        pid = self.process.pid
        self.doh.failing = True
        with self.assertRaises(ValueError):
            live.dns_query(self.dns_port,'failure.example')
        self.assertEqual(set(self.dns.names),{'proxy.test'})
        self.doh.failing = False
        self.assertEqual(live.dns_query(self.dns_port,'recovered.example'),'203.0.113.7')
        self.assertEqual(self.process.pid,pid)

    def test_actual_button_probe_uses_tunnel_and_rejects_servfail(self):
        pid = self.process.pid
        for failing, expected in ((False,1),(True,0),(False,1)):
            self.doh.failing = failing
            with patch.dict(os.environ, {'CURL_CA_BUNDLE':str(self.cert)}):
                p = self.fixture.fixture.shell('''
config_set settings cloudflare_dns_patch 1
config_set settings config_path ''' + shlex.quote(str(self.config)) + '''
cloudflare_status
''')
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(json.loads(p.stdout), {'mode':'cloudflare_proxy','dns_status':expected})
        self.assertIn('1.1.1.1',self.proxy.targets)
        self.assertEqual(set(self.dns.names),{'proxy.test'})
        self.assertEqual(self.process.pid,pid)
