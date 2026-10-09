"""Separate local FakeIP failures from an unavailable public diagnostic server."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PORT = Path(__file__).resolve().parents[1]
LIB = PORT / 'overlay/package/openstick/podkop/files/usr/lib'
UI = PORT / 'overlay/package/openstick/luci-app-podkop/htdocs/luci-static/resources/view/podkop'


class FakeIPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.program = self.tmp / 'check.sh'
        self.program.write_text((LIB / 'ufi_fakeip_check.sh').read_text().replace(
            '/usr/lib/podkop/ufi_policy_routing.sh', str(self.tmp / 'policy.sh')))
        (self.tmp / 'policy.sh').write_text('exit "${TEST_ROUTE_EXIT:-0}"\n')
        for name, body in {
            'dig': '''#!/bin/sh
case "$*" in
  *127.0.0.42*) printf '%s\\n' "$TEST_DNS" ;;
  *127.0.0.1*) printf '%s\\n' "$TEST_ROUTER_DNS" ;;
  *) exit 1 ;;
esac
''',
            'curl': '''#!/bin/sh
printf '%s' "$TEST_EXTERNAL"
exit "${TEST_CURL_EXIT:-0}"
'''
        }.items():
            p = self.tmp / name
            p.write_text(body)
            p.chmod(0o755)

    def check(self, ip='198.18.0.4', router=None, external='', **extra):
        env = dict(os.environ, PATH=str(self.tmp) + os.pathsep + os.environ['PATH'],
                   TEST_DNS=ip, TEST_ROUTER_DNS=ip if router is None else router,
                   TEST_EXTERNAL=external, **extra)
        proc = subprocess.run(['sh', str(self.program)], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_unavailable_public_endpoint_is_unknown_not_false(self):
        result = self.check(TEST_CURL_EXIT='7')
        self.assertTrue(result['local_fakeip'])
        self.assertTrue(result['dnsmasq_fakeip'])
        self.assertTrue(result['policy_route'])
        self.assertFalse(result['external_available'])
        self.assertIsNone(result['fakeip'])

    def test_both_halves_of_fakeip_15_and_invalid_answers(self):
        for ip, expected in [('198.18.0.1', True), ('198.19.255.254', True),
                             ('198.17.0.1', False), ('198.20.0.1', False),
                             ('198.18.300.1', False), ('198.18.0.2.attacker.test', False),
                             (';; connection timed out; no servers could be reached', False)]:
            with self.subTest(ip=ip):
                self.assertEqual(self.check(ip)['local_fakeip'], expected)

    def test_dnsmasq_failure_is_not_hidden_by_working_singbox(self):
        result = self.check(router='92.118.148.106')
        self.assertTrue(result['local_fakeip'])
        self.assertFalse(result['dnsmasq_fakeip'])

    def test_routing_failure_is_not_hidden_by_fakeip_answer(self):
        result = self.check(TEST_ROUTE_EXIT='1')
        self.assertTrue(result['local_fakeip'])
        self.assertFalse(result['policy_route'])

    def test_valid_negative_external_response_is_still_negative(self):
        result = self.check(external='{"fakeip":false,"IP":"192.0.2.1"}')
        self.assertTrue(result['external_available'])
        self.assertIs(result['fakeip'], False)
        good = self.check(external='{"fakeip":true}')
        self.assertIs(good['fakeip'], True)

    def test_bad_json_or_error_page_is_not_a_fakeip_result(self):
        for data in ('<html>upstream unavailable</html>', '{}', '{"fakeip":"true"}'):
            with self.subTest(data=data):
                self.assertFalse(self.check(external=data)['external_available'])

    def test_luci_does_not_mark_endpoint_outage_as_client_failure(self):
        source = (UI / 'main.js').read_text()
        fn = source[source.index('async function runFakeIPCheck()'):source.index('// src/partials/button/styles.ts')]
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required by the UI regression gate')
        script = '''const assert = require('assert');
const _ = text => text;
const DIAGNOSTICS_CHECKS_MAP = {FAKEIP:{order:1,title:'FakeIP',code:'fakeip'}};
let result;
const updateCheckStore = value => { result = value; };
let data = {version:1,local_fakeip:true,dnsmasq_fakeip:true,policy_route:true,
            external_available:false,fakeip:null};
const PodkopShellMethods = {checkFakeIP:async()=>({success:true,data})};
const RemoteFakeIPMethods = {getFakeIpCheck:async()=>({success:false}),getIpCheck:async()=>({success:false})};
''' + fn + '''
(async () => {
 await runFakeIPCheck();
 assert.strictEqual(result.state, 'warning');
 assert.strictEqual(result.items[0].state, 'success');
 assert.strictEqual(result.items[4].state, 'warning');
 assert.match(result.items[4].value, /unknown/);
 data.policy_route = false;
 await runFakeIPCheck();
 assert.strictEqual(result.state, 'error');
 assert.strictEqual(result.items[2].state, 'error');
 data.policy_route = true; data.external_available = true; data.fakeip = false;
 await runFakeIPCheck();
 assert.strictEqual(result.state, 'error');
})().catch(error => { console.error(error); process.exit(1); });
'''
        p = subprocess.run([node, '-e', script], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == '__main__':
    unittest.main()
