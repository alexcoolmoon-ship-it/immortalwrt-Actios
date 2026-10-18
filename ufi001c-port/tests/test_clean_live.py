"""Live DNS/FakeIP exchange with the built engine and loopback-only test servers.

No modem, host routes, firewall, DNS settings or outside proxy are touched.
The generated config is normalized by the production writer. Only listeners,
upstream DNS, the proxy endpoint and cache paths are relocated for isolation.
"""
import concurrent.futures
import ipaddress
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import socketserver
import struct
import subprocess
import threading
import time
import unittest
import test_clean as generator


def receive(sock, count):
    result = b''
    while len(result) < count:
        data = sock.recv(count - len(result))
        if not data:
            raise EOFError('closed connection')
        result += data
    return result


def dns_query(port, name, timeout=5):
    identifier = os.urandom(2)
    question = b''.join(bytes([len(v)]) + v.encode() for v in name.split('.')) + b'\0\0\1\0\1'
    query = identifier + bytes.fromhex('01000001000000000000') + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(query, ('127.0.0.1', port))
        answer = sock.recv(4096)
    if answer[:2] != identifier or len(answer) < 12:
        raise ValueError('Invalid DNS response')
    if answer[3] & 15:
        raise ValueError('DNS error ' + str(answer[3] & 15))
    if struct.unpack_from('!H', answer, 6)[0] < 1:
        raise ValueError('DNS answer missing')
    return str(ipaddress.IPv4Address(answer[-4:]))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class DNSHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        try:
            while True:
                size = struct.unpack('!H', receive(self.request, 2))[0]
                query = receive(self.request, size)
                self.server.queries += 1
                end = 12
                while query[end]:
                    end += query[end] + 1
                question = query[12:end + 5]
                if self.server.failing:
                    answer = query[:2] + bytes.fromhex('81820001000000000000') + question
                else:
                    answer = query[:2] + bytes.fromhex('81800001000100000000') + question
                    answer += bytes.fromhex('c00c000100010000003c0004') + socket.inet_aton('203.0.113.7')
                self.request.sendall(struct.pack('!H', len(answer)) + answer)
        except (EOFError, TimeoutError, ConnectionError, OSError):
            pass


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            version, count = receive(self.request, 2)
            if version != 5:
                raise ValueError('SOCKS version')
            receive(self.request, count)
            self.request.sendall(b'\5\0')
            version, command, reserved, kind = receive(self.request, 4)
            if kind == 3:
                target = receive(self.request, receive(self.request, 1)[0]).decode()
            elif kind == 1:
                target = socket.inet_ntoa(receive(self.request, 4))
            else:
                raise ValueError('Unexpected SOCKS address')
            receive(self.request, 2)
            self.server.targets.append(target)
            self.request.sendall(b'\5\0\0\1' + socket.inet_aton('127.0.0.1') + b'\0\0')
            self.request.recv(4096)
            self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
        except (EOFError, TimeoutError, ConnectionError, OSError):
            pass


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get('UFI_TEST_ENGINE'), 'live checks run with the built ARM64 engine')
class LiveCleanTests(unittest.TestCase):
    def setUp(self):
        self.fixture = generator.CleanTests('test_real_generator_keeps_dns_direct_with_tcp_and_fakeip_cache')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tmp = self.fixture.tmp
        self.dns = Server(('127.0.0.1', 0), DNSHandler)
        self.dns.queries = 0
        self.dns.failing = False
        self.proxy = Server(('127.0.0.1', 0), ProxyHandler)
        self.proxy.targets = []
        for server in (self.dns, self.proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.dns_port, self.mixed_port = free_port(), free_port()
        output = self.tmp / 'normalized.json'
        rulesets = self.tmp / 'rule-sets'
        rulesets.mkdir()
        p = self.fixture.shell('TMP_RULESET_FOLDER=' + shlex.quote(str(rulesets)) + '''
config_set main user_domain_list_type dynamic
config_set main user_domains proxied.test
sing_box_save_config() { sing_box_cm_save_config_to_file "$config" ''' + shlex.quote(str(output)) + '''; }
sing_box_init_config
''')
        self.assertEqual(p.returncode, 0, p.stderr)
        config = json.loads(output.read_text())
        config['log']['level'] = 'debug'
        local_sets = [r for r in config['route'].get('rule_set', []) if r.get('type') == 'local']
        self.assertTrue(local_sets, 'Production generator did not create the local domain rule set: ' + p.stderr)
        self.assertIn('proxied.test', json.loads(Path(local_sets[0]['path']).read_text())['rules'][0]['domain_suffix'])
        for server in config['dns']['servers']:
            if server['type'] != 'fakeip':
                server.update(server='127.0.0.1', server_port=self.dns.server_address[1])
        config['inbounds'] = [
            {'type': 'direct', 'tag': 'dns-in', 'listen': '127.0.0.1', 'listen_port': self.dns_port},
            {'type': 'mixed', 'tag': 'tproxy-in', 'listen': '127.0.0.1', 'listen_port': self.mixed_port},
        ]
        for outbound in config['outbounds']:
            if outbound['tag'] == 'main-out':
                outbound.clear()
                outbound.update(type='socks', tag='main-out', server='127.0.0.1', server_port=self.proxy.server_address[1], version='5')
        config['route']['auto_detect_interface'] = False
        config['experimental'].pop('clash_api', None)
        config['experimental']['cache_file']['path'] = str(self.tmp / 'cache.db')
        self.config = self.tmp / 'runtime.json'
        self.config.write_text(json.dumps(config))
        self.command = shlex.split(os.environ['UFI_TEST_ENGINE'])
        self.process = None
        self.log = (self.tmp / 'engine.log').open('w+')
        self.addCleanup(self.log.close)
        self.addCleanup(self.stop_engine)
        self.start_engine()

    def stop_engine(self):
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
                self.fail('Engine did not shut down in 15 seconds')

    def start_engine(self):
        self.process = subprocess.Popen(self.command + ['run', '-c', str(self.config)], stdout=self.log, stderr=self.log)
        for _ in range(40):
            if self.process.poll() is not None:
                self.log.seek(0)
                self.fail('Engine exited: ' + self.log.read()[-4000:])
            try:
                if dns_query(self.dns_port, 'ready.example', timeout=.25) == '203.0.113.7':
                    return
            except (OSError, ValueError):
                time.sleep(.05)
        self.fail('Engine DNS did not become ready')

    def http_via_fakeip(self, address):
        with socket.create_connection(('127.0.0.1', self.mixed_port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(b'\5\1\0')
            self.assertEqual(receive(sock, 2), b'\5\0')
            sock.sendall(b'\5\1\0\1' + socket.inet_aton(address) + struct.pack('!H', 80))
            response = receive(sock, 4)
            self.assertEqual(response[1], 0)
            if response[3] == 1:
                receive(sock, 6)
            elif response[3] == 3:
                receive(sock, receive(sock, 1)[0] + 2)
            elif response[3] == 4:
                receive(sock, 18)
            # No domain is provided by the client: the engine must recover it
            # from its FakeIP mapping, including after a process restart.
            sock.sendall(b'UFI_PROBE\r\n\r\n')
            try:
                self.assertIn(b'200 OK', sock.recv(4096))
            except (TimeoutError, OSError) as error:
                self.fail(str(error) + '; proxy targets=' + repr(self.proxy.targets)
                          + '; engine log=' + Path(self.log.name).read_text()[-6000:])
        self.assertEqual(self.proxy.targets[-1], 'proxied.test')

    def test_fakeip_reverse_mapping_survives_engine_restart(self):
        address = dns_query(self.dns_port, 'proxied.test')
        self.assertIn(ipaddress.ip_address(address), ipaddress.ip_network('198.18.0.0/15'))
        self.http_via_fakeip(address)
        self.stop_engine()
        self.start_engine()
        self.http_via_fakeip(address)
        self.assertEqual(dns_query(self.dns_port, 'proxied.test'), address)

    def test_dns_recovers_after_upstream_servfail_without_service_restart(self):
        original_pid = self.process.pid
        self.dns.failing = True
        with self.assertRaises(ValueError):
            dns_query(self.dns_port, 'failure.example')
        self.assertIn(ipaddress.ip_address(dns_query(self.dns_port, 'proxied.test')), ipaddress.ip_network('198.18.0.0/15'))
        self.dns.failing = False
        self.assertEqual(dns_query(self.dns_port, 'recovered.example'), '203.0.113.7')
        self.assertEqual(self.process.pid, original_pid)
        self.assertIsNone(self.process.poll())

    def test_concurrent_dns_with_unavailable_proxy(self):
        # An unused loopback port makes the proxy unreachable, as in a dropped
        # transport, without changing the DNS path or restarting the engine.
        config = json.loads(self.config.read_text())
        next(o for o in config['outbounds'] if o['tag'] == 'main-out')['server_port'] = free_port()
        self.stop_engine()
        self.config.write_text(json.dumps(config))
        self.start_engine()
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            answers = list(executor.map(lambda n: dns_query(self.dns_port, 'load-' + str(n) + '.example'), range(1000)))
        self.assertEqual(answers, ['203.0.113.7'] * 1000)
        self.assertIsNone(self.process.poll())


if __name__ == '__main__':
    unittest.main()
