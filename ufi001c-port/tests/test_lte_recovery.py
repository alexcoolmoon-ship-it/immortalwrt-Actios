"""Fault injection for the reported connected/netifd-up but kernel-DOWN case.

All commands affecting radio or host networking are substituted with a stateful
fake. Production shell/jq logic is executed, including idempotence and failures.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PORT = Path(__file__).resolve().parents[1]
PKG = PORT / 'overlay/package/openstick'
HEALTH = PKG / 'ufi-lte-health/files'
PROTO = PKG / 'modemmanager/files/lib/netifd/proto/modemmanager.sh'

STATE = {
    'cfg': {'network.modem.proto': 'modemmanager', 'network.modem.device': 'qcom-soc',
            'ufi-lte-health.main.enabled': '1'},
    'net': {'up': True, 'pending': False, 'available': True, 'autostart': True,
            'proto': 'modemmanager', 'uptime': 49, 'l3_device': 'wwan0', 'metric': 0,
            'ipv4-address': [{'address': '10.89.96.34', 'mask': 30}],
            'route': [{'target': '0.0.0.0', 'mask': 0, 'nexthop': '10.89.96.33',
                       'source': '10.89.96.34/32'}]},
    'modem': {'modem': {'generic': {'state': 'connected', 'bearers':
                ['/org/freedesktop/ModemManager1/Bearer/3']}}},
    'bearer': {'bearer': {'status': {'connected': 'yes', 'suspended': 'no', 'interface': 'wwan0'},
                        'ipv4-config': {'method': 'static', 'address': '10.89.96.34',
                                        'prefix': '30', 'gateway': '10.89.96.33'}}},
    'link': [{'flags': ['BROADCAST', 'MULTICAST'], 'ifname': 'wwan0'}],
    'addr': [{'addr_info': [{'local': '10.89.96.34', 'prefixlen': 30, 'scope': 'global'}]}],
    'routes': [], 'calls': [], 'writes': [], 'mode': '',
}

FAKE = r'''import json, os, pathlib, sys
p = pathlib.Path(os.environ['TEST_STATE']); s = json.loads(p.read_text())
cmd = pathlib.Path(sys.argv[0]).name; a = sys.argv[1:]; rc = 0
s['calls'].append([cmd] + a)
if cmd == 'uci':
    key = a[-1]
    if key in s['cfg']: print(s['cfg'][key])
    else: rc = 1
elif cmd == 'ubus':
    if a != ['-t','5','call','network.interface.modem','status']: raise AssertionError(a)
    print(json.dumps(s['net']))
elif cmd == 'mmcli':
    if '--output-json' in a:
        if '--modem=qcom-soc' in a: print(json.dumps(s['modem']))
        else:
            s['bearer_reads'] = s.get('bearer_reads', 0) + 1
            if s['mode'] == 'reattach' and s['bearer_reads'] > 1:
                s['bearer']['bearer']['ipv4-config']['address'] = '10.89.96.38'
            print(json.dumps(s['bearer']))
    elif s['mode'] == 'band_fail' and any(x.startswith('--set-current-bands=') for x in a): rc = 1
elif cmd == 'ip':
    if a[:4] == ['-j','link','show','dev']: print(json.dumps(s['link']))
    elif a[:4] == ['-j','-4','address','show']: print(json.dumps(s['addr']))
    elif a[:4] == ['-j','-4','route','show']: print(json.dumps(s['routes']))
    elif a[:2] == ['link','set']:
        s['writes'].append(a)
        if s['mode'] == 'link_fail': rc = 1
        elif a[-1] == 'up': s['link'][0]['flags'].append('UP')
    elif a[:3] == ['-4','address','add']:
        s['writes'].append(a); addr, prefix = a[3].split('/')
        s['addr'][0]['addr_info'].append({'local':addr,'prefixlen':int(prefix),'scope':'global'})
    elif a[:3] == ['-4','route','replace']:
        s['writes'].append(a)
        assert a[-2:] == ['table','254']; assert 'onlink' in a
        if 'UP' not in s['link'][0]['flags']: rc = 2
        else: s['routes'] = [{'dst':'default','gateway':a[a.index('via')+1],
                             'dev':a[a.index('dev')+1],'metric':int(a[a.index('metric')+1])}]
    else: raise AssertionError(a)
elif cmd == 'logger': pass
else: raise AssertionError((cmd,a))
p.write_text(json.dumps(s)); sys.exit(rc)
'''


class LTERecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        self.path = self.tmp / 'state.json'
        self.state = copy.deepcopy(STATE)
        self.save()
        self.env = dict(os.environ, TEST_STATE=str(self.path),
                        PATH=str(self.tmp) + os.pathsep + os.environ['PATH'])
        for name in ('uci', 'ubus', 'mmcli', 'ip', 'logger'):
            p = self.tmp / name
            p.write_text('#!' + sys.executable + '\n' + FAKE)
            p.chmod(0o755)
        (self.tmp / 'board').write_text('thwc,ufi001c\n')
        (self.tmp / 'uptime').write_text('200.00 100.00\n')
        # Relocate data paths only; leave command and shell logic intact.
        text = (HEALTH / 'ufi-lte-health').read_text()
        for old, new in {
            '/tmp/sysinfo/board_name': self.tmp / 'board',
            '/proc/uptime': self.tmp / 'uptime',
            '/var/run/ufi-lte-health': self.tmp / 'guard',
            '/usr/share/ufi-lte-health/session.jq': HEALTH / 'session.jq',
        }.items():
            text = text.replace(old, str(new))
        self.script = self.tmp / 'guard.sh'
        self.script.write_text(text)
        self.proto = self.tmp / 'proto.sh'
        # Executable prerequisite checks are not available on the host.
        self.proto.write_text(PROTO.read_text().replace('[ -x /usr/bin/mmcli ] || exit 0', ':')
                              .replace('[ -x /usr/sbin/pppd ] || exit 0', ':'))

    def save(self):
        self.path.write_text(json.dumps(self.state))

    def run_health(self, command='repair'):
        return subprocess.run(['sh', str(self.script), command], env=self.env,
                              capture_output=True, text=True, timeout=15)

    def result(self):
        return json.loads(self.path.read_text())

    def run_proto(self, command):
        return subprocess.run(['sh', '-c', '''
INCLUDE_ONLY=1
. "$1"
proto_notify_error() { echo "ERROR:$*"; }
proto_init_update() { echo "ANNOUNCED_UP:$*"; }
proto_set_keep() { :; }
proto_add_ipv4_address() { :; }
proto_add_ipv4_route() { :; }
proto_add_dns_server() { :; }
json_add_int() { :; }
proto_send_update() { echo "SENT_UPDATE:$*"; }
''' + command, 'sh', str(self.proto)], env=self.env, capture_output=True, text=True)

    def test_reported_kernel_down_case_recovers_from_live_bearer(self):
        p = self.run_health()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        s = self.result()
        self.assertEqual(s['writes'][0], ['link','set','dev','wwan0','up'])
        self.assertIn('10.89.96.33', s['writes'][-1])
        self.assertIn('kernel IPv4 restored', p.stdout)
        n = len(s['writes'])
        self.assertEqual(self.run_health().returncode, 0)
        self.assertEqual(len(self.result()['writes']), n, 'healthy poll must not write')

    def test_status_is_read_only_and_detects_link_down(self):
        p = self.run_health('status')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('link=0', p.stdout)
        self.assertEqual(self.result()['writes'], [])
        self.assertFalse(any(a[0] == 'mmcli' for a in self.result()['calls']))

    def test_openwrt_jq_without_regex_handles_recovery_and_band_policy(self):
        # The device's small jq throws at test/match/sub. Reproduce that
        # capability boundary while keeping real JSON operations and shell.
        real_jq = shutil.which('jq', path=os.environ.get('PATH', ''))
        self.assertIsNotNone(real_jq)
        fakebin = self.tmp / 'jq-lite'; fakebin.mkdir()
        wrapper = fakebin / 'jq'
        wrapper.write_bytes((PORT / 'tests/fixtures/jq-no-regex.py').read_bytes())
        wrapper.chmod(0o755)
        self.env['PATH'] = str(fakebin) + os.pathsep + self.env['PATH']
        self.env['UFI_TEST_REAL_JQ'] = os.environ.get('UFI_TEST_REAL_JQ', real_jq)
        p = subprocess.run([str(wrapper), '-n', '"" | test("x")'],
                           env=self.env, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('ONIGURUMA', p.stderr)
        p = self.run_health()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('kernel IPv4 restored', p.stdout)
        self.state = self.result()
        self.state['modem']['modem']['generic']['supported-bands'] = [
            'egsm', 'eutran-1', 'eutran-3', 'eutran-7', 'eutran-20']
        self.save()
        p = self.run_proto('modemmanager_resolve_lte_bands qcom-soc supported-lte eutran-20')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(set(p.stdout.strip().split('|')), {'eutran-1', 'eutran-3', 'eutran-7'})

    def test_missing_address_is_restored_only_from_matching_bearer(self):
        self.state['addr'][0]['addr_info'] = []; self.save()
        p = self.run_health()
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(self.result()['writes'][1],
                         ['-4','address','add','10.89.96.34/30','dev','wwan0'])

    def test_new_session_uses_new_address_and_interface(self):
        self.state['net']['l3_device'] = 'wwan2'
        self.state['link'][0]['ifname'] = 'wwan2'
        self.state['net']['ipv4-address'][0]['address'] = '10.10.10.6'
        self.state['net']['route'][0].update(nexthop='10.10.10.5', source='10.10.10.6/32')
        self.state['bearer']['bearer']['status']['interface'] = 'wwan2'
        self.state['bearer']['bearer']['ipv4-config'].update(address='10.10.10.6', gateway='10.10.10.5')
        self.state['addr'][0]['addr_info'][0]['local'] = '10.10.10.6'; self.save()
        p = self.run_health()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        writes = json.dumps(self.result()['writes'])
        self.assertIn('10.10.10.5', writes); self.assertIn('wwan2', writes)
        self.assertNotIn('10.89.96.', writes)

    def test_disconnected_suspended_stale_or_reattaching_bearer_never_written(self):
        cases = ['disconnected', 'suspended', 'stale', 'reattach', 'multi_bearer']
        for mode in cases:
            with self.subTest(mode=mode):
                self.state = copy.deepcopy(STATE)
                if mode == 'disconnected': self.state['bearer']['bearer']['status']['connected'] = 'no'
                if mode == 'suspended': self.state['bearer']['bearer']['status']['suspended'] = 'yes'
                if mode == 'stale': self.state['bearer']['bearer']['ipv4-config']['address'] = '10.89.96.38'
                if mode == 'reattach': self.state['mode'] = mode
                if mode == 'multi_bearer': self.state['modem']['modem']['generic']['bearers'] *= 2
                self.save()
                self.assertNotEqual(self.run_health().returncode, 0)
                self.assertEqual(self.result()['writes'], [])

    def test_administrative_down_pending_and_custom_table_are_respected(self):
        for field, value in [('up',False),('autostart',False),('pending',True),('uptime',2)]:
            with self.subTest(field=field):
                self.state = copy.deepcopy(STATE); self.state['net'][field] = value; self.save()
                self.assertNotEqual(self.run_health().returncode, 0)
                self.assertEqual(self.result()['writes'], [])
        for key, value in [('auto','0'),('autoconnect','0'),('defaultroute','0'),('ip4table','100')]:
            with self.subTest(key=key):
                self.state = copy.deepcopy(STATE); self.state['cfg']['network.modem.'+key] = value; self.save()
                self.assertNotEqual(self.run_health().returncode, 0)
                self.assertEqual(self.result()['writes'], [])

    def test_other_board_interface_address_and_route_are_not_overwritten(self):
        for mode in ['board','interface','address','route','invalid_address']:
            with self.subTest(mode=mode):
                self.state = copy.deepcopy(STATE)
                (self.tmp/'board').write_text('thwc,ufi001c' if mode != 'board' else 'other')
                if mode == 'interface': self.state['net']['l3_device'] = 'br-lan'
                if mode == 'address': self.state['addr'][0]['addr_info'][0]['local'] = '10.89.96.38'
                if mode == 'route': self.state['routes'] = [{'gateway':'192.0.2.1','metric':0}]
                if mode == 'invalid_address': self.state['net']['ipv4-address'][0]['address'] = '999.1.1.1'
                self.save(); self.assertNotEqual(self.run_health().returncode, 0)
                self.assertEqual(self.result()['writes'], [])

    def test_failure_does_not_set_route_and_attempts_are_rate_limited(self):
        self.state['mode'] = 'link_fail'; self.save()
        for seconds in (200, 201, 260, 320, 380):
            (self.tmp/'uptime').write_text(f'{seconds}.00 100.00\n')
            self.assertNotEqual(self.run_health().returncode, 0)
        s = self.result()
        self.assertEqual(len(s['writes']), 3)
        self.assertTrue(all(a[0] == 'link' for a in s['writes']))
        (self.tmp/'uptime').write_text('801.00 100.00\n')
        self.run_health()
        self.assertEqual(len(self.result()['writes']), 4)

    def test_proto_does_not_announce_up_when_link_up_fails(self):
        self.state['mode'] = 'link_fail'; self.save()
        p = self.run_proto('modemmanager_connected_method_static_ipv4 modem wwan0 10.1.1.2 30 10.1.1.1 "" 1.1.1.1 "" 0')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('LINK_UP_FAILED', p.stdout)
        self.assertNotIn('ANNOUNCED_UP', p.stdout)

    def test_invalid_budget_state_stops_without_arithmetic_or_network_writes(self):
        for text in ('', '\n', '1 2\n', '1 2 3 extra\n', '001 2 3\n',
                     '1 2 9999999999999999999999\n', '100 1 99\n', '100 1 500\n'):
            with self.subTest(text=text):
                (self.tmp/'guard.state').write_text(text)
                p = self.run_health()
                self.assertNotEqual(p.returncode, 0)
                self.assertEqual(p.stderr, '')
                self.assertEqual(self.result()['writes'], [])
                self.assertEqual((self.tmp/'guard.state').read_text(), text)

    def test_proto_missing_address_is_failure(self):
        p = self.run_proto('modemmanager_connected_method_static_ipv4 modem wwan0 "" 30 10.1.1.1 "" "" "" 0')
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn('ANNOUNCED_UP', p.stdout)
        self.assertEqual(self.result()['writes'], [])

    def test_proto_raises_device_before_reporting_success(self):
        p = self.run_proto('modemmanager_connected_method_static_ipv4 modem wwan0 10.1.1.2 30 10.1.1.1 1500 1.1.1.1 "" 0')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('SENT_UPDATE', p.stdout)
        self.assertIn('UP', self.result()['link'][0]['flags'])

    def test_band_profile_is_one_quoted_argument_and_failure_propagates(self):
        # Substitute mode setter's network/proto callbacks only.
        cmd = '''modemmanager_set_allowed_mode() { mmcli --set-allowed-modes="$3"; }
modemmanager_apply_band_profile modem qcom-soc 'eutran-1|eutran-3|eutran-7' 4g '' '''
        p = self.run_proto(cmd)
        self.assertEqual(p.returncode, 0, p.stderr)
        calls = [a for a in self.result()['calls'] if a[0] == 'mmcli']
        self.assertEqual(calls[0], ['mmcli','--set-allowed-modes=4g'])
        self.assertIn('--set-current-bands=eutran-1|eutran-3|eutran-7', calls[1])
        self.state['mode'] = 'band_fail'; self.save()
        p = self.run_proto(cmd)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('MM_BAND_PROFILE_FAILED', p.stdout)

    def test_lte_exclusions_keep_every_other_supported_band(self):
        supported = ['egsm', 'utran-1', 'eutran-1', 'eutran-3', 'eutran-5', 'eutran-7',
                     'eutran-8', 'eutran-20', 'eutran-38', 'eutran-39', 'eutran-40', 'eutran-41']
        self.state['modem']['modem']['generic']['supported-bands'] = supported; self.save()
        # B5 is a synthetic exclusion, NOT evidence that B5 is bad on a UFI.
        p = self.run_proto("modemmanager_resolve_lte_bands qcom-soc supported-lte eutran-5")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(set(p.stdout.strip().split('|')), set(supported[2:]) - {'eutran-5'})
        self.assertFalse(any(any(x.startswith('--set-') for x in c) for c in self.result()['calls']))
        for excluded in ('eutran-19', 'eutran-3|', 'any', '|eutran-3'):
            self.assertNotEqual(self.run_proto(
                'modemmanager_resolve_lte_bands qcom-soc supported-lte "' + excluded + '"').returncode, 0)

    def test_missing_supported_bands_and_excluding_every_band_fail_closed(self):
        self.assertNotEqual(self.run_proto('modemmanager_resolve_lte_bands qcom-soc supported-lte ""').returncode, 0)
        self.state['modem']['modem']['generic']['supported-bands'] = ['eutran-3']; self.save()
        self.assertNotEqual(self.run_proto('modemmanager_resolve_lte_bands qcom-soc supported-lte eutran-3').returncode, 0)
        self.assertNotEqual(self.run_proto("modemmanager_resolve_lte_bands qcom-soc 'eutran-1|eutran-3|eutran-7' eutran-3").returncode, 0)

    def test_observed_two_band_policy_does_not_remove_unregistered_bands(self):
        self.state['modem']['modem']['generic']['supported-bands'] = [
            'egsm','utran-1','eutran-1','eutran-3','eutran-5','eutran-7','eutran-8',
            'eutran-20','eutran-38','eutran-39','eutran-40','eutran-41']
        self.save()
        p = self.run_proto("modemmanager_resolve_lte_bands qcom-soc supported-lte 'eutran-7|eutran-20'")
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertEqual(set(p.stdout.strip().split('|')), {
            'eutran-1','eutran-3','eutran-5','eutran-8',
            'eutran-38','eutran-39','eutran-40','eutran-41'})


if __name__ == '__main__':
    unittest.main()
