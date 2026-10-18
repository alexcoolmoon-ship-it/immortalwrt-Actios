"""Run the real recovery and DNS probes with isolated network/service stubs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PORT = Path(__file__).resolve().parents[1]
LIB = PORT / 'overlay/package/openstick/podkop/files/usr/lib'
PODKOP = PORT / 'overlay/package/openstick/podkop/files/usr/bin/podkop'


class DNSHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                        TEST_ROOT=str(self.root))
        self.config = {'dns': {'servers': [
            {'tag': 'dns-server', 'type': 'https', 'server': '1.1.1.1', 'detour': 'main-out'},
            {'tag': 'bootstrap-dns-server', 'type': 'udp', 'server': '77.88.8.8'}]},
            'outbounds': [{'tag': 'main-out', 'type': 'vless', 'server': 'proxy.example'}],
            'inbounds': [{'tag': 'service-mixed-in', 'type': 'mixed',
                          'listen': '127.0.0.1', 'listen_port': 4534}],
            'route': {'rules': [{'inbound': ['service-mixed-in'], 'outbound': 'main-out'}]}}
        (self.root / 'config.json').write_text(json.dumps(self.config))
        (self.root / 'board_name').write_text('thwc,ufi001c\n')
        (self.root / 'ufi-podkop.active').touch()
        self.clock(1000)
        self.executable('uci', '''import os, pathlib, sys
r=pathlib.Path(os.environ['TEST_ROOT']); key=sys.argv[-1]
values={'podkop.settings.ufi_dns_health':os.environ.get('GUARD_ENABLED','1'),
 'podkop.settings.ufi_dns_proxy_section':'main',
 'podkop.settings.shutdown_correctly':os.environ.get('SHUTDOWN','0'),
 'podkop.settings.config_path':str(r/'config.json'),
 'podkop.settings.ufi_bootstrap_transport':os.environ.get('TRANSPORT','tcp'),
 'sing-box.main.enabled':'0' if (r/'stopped').exists() else os.environ.get('ENGINE_ENABLED','1')}
if key in values: print(values[key])
else: sys.exit(1)
''')
        self.executable('lte', "import os,sys\nsys.exit(0 if os.environ.get('LTE_OK','1')=='1' else 1)\n")
        self.executable('logger', 'pass\n')
        self.executable('pidof', 'print(999999)\n')
        self.executable('sing-box', "import os,sys\nsys.exit(0 if os.environ.get('CONFIG_OK','1')=='1' else 1)\n")
        self.executable('curl', '''import os,pathlib,sys
r=pathlib.Path(os.environ['TEST_ROOT'])
if os.environ.get('STOP_DURING_PROBE')=='1': (r/'stopped').touch()
if os.environ.get('CHANGE_DURING_PROBE')=='1':
 p=r/'config.json'; p.write_text(p.read_text()+' ')
if os.environ.get('EXTERNAL_OK','1')!='1': sys.exit(22)
sys.stdout.buffer.write(bytes.fromhex('000081800001000100000000'))
''')
        self.executable('dig', '''import os,sys
args=sys.argv[1:]
mode=os.environ.get('DIG_MODE')
if mode=='timeout': sys.exit(9)
if mode in ('SERVFAIL','REFUSED','empty'):
 print(';; ->>HEADER<<- opcode: QUERY, status: '+('NOERROR' if mode=='empty' else mode)+', id: 1'); sys.exit(0)
key='ENGINE_OK' if '@127.0.0.42' in args else 'ROUTER_OK'
if os.environ.get(key,'1')!='1': print(';; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 1'); sys.exit(0)
name=next((a for a in args if a.endswith('example.com')), 'example.com')
print(';; ->>HEADER<<- opcode: QUERY, status: '+('NOERROR' if name=='example.com' else 'NXDOMAIN')+', id: 1')
if name=='example.com': print('example.com. 60 IN A 203.0.113.1')
''')
        for svc in ('sing-box', 'dnsmasq'):
            self.executable('init/' + svc, '''import os,pathlib,sys
r=pathlib.Path(os.environ['TEST_ROOT'])
assert sys.argv[1:]==['restart']
with (r/'actions').open('a') as f: f.write(pathlib.Path(__file__).name+'\\n')
''')
        code = (LIB / 'ufi_dns_health.sh').read_text()
        for before, after in [('/var/run/',str(self.root)+'/'),
             ('/proc/uptime',str(self.root/'uptime')),
             ('/tmp/sysinfo/board_name',str(self.root/'board_name')),
             ('/usr/sbin/ufi-lte-health',str(self.root/'lte')),
             ('/usr/lib/podkop/ufi_dns_tunnel.sh',str(LIB/'ufi_dns_tunnel.sh')),
             ('/etc/init.d/',str(self.root/'init')+'/')]:
            code=code.replace(before,after)
        self.script=self.root/'health.sh'; self.script.write_text(code)

    def executable(self, name, content):
        path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('#!'+sys.executable+'\n'+content); path.chmod(0o755)

    def clock(self, seconds):
        (self.root/'uptime').write_text(str(seconds)+'.00 0.00\n')

    def tick(self, **env):
        p=subprocess.run(['sh',str(self.script),'tick'],env=dict(self.env,**env),
                         text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def actions(self):
        p=self.root/'actions'
        return p.read_text().splitlines() if p.exists() else []

    def fail_three(self, **env):
        for t in (1000,1030,1060): self.clock(t); self.tick(**env)

    def test_hung_external_dns_is_detected_despite_cached_local_success(self):
        self.tick(EXTERNAL_OK='0'); self.assertEqual(self.actions(),[])
        self.clock(1030); self.tick(EXTERNAL_OK='0'); self.assertEqual(self.actions(),[])
        self.clock(1060); self.tick(EXTERNAL_OK='0')
        self.assertEqual(self.actions(),['sing-box'])
        snapshot=(self.root/'ufi-dns-health.last-failure').read_text()
        self.assertIn('external_doh=0 local_engine_dns=1 local_router_dns=1',snapshot)
        self.assertNotIn('proxy.example',snapshot)

    def test_only_dnsmasq_restarts_if_engine_and_external_dns_are_healthy(self):
        self.fail_three(ROUTER_OK='0')
        self.assertEqual(self.actions(),['dnsmasq'])

    def test_internal_resolver_servfail_restarts_engine(self):
        self.fail_three(ENGINE_OK='0')
        self.assertEqual(self.actions(),['sing-box'])

    def test_success_breaks_failure_streak(self):
        self.tick(EXTERNAL_OK='0'); self.tick(EXTERNAL_OK='0'); self.tick()
        self.tick(EXTERNAL_OK='0'); self.tick(EXTERNAL_OK='0')
        self.assertEqual(self.actions(),[])

    def test_disabled_intentionally_stopped_or_bad_lte_never_restarts(self):
        for params in ({'GUARD_ENABLED':'0'},{'ENGINE_ENABLED':'0'},{'LTE_OK':'0'},{'SHUTDOWN':'1'}):
            self.fail_three(EXTERNAL_OK='0',**params)
        (self.root/'ufi-podkop.active').unlink()
        self.fail_three(EXTERNAL_OK='0')
        self.assertEqual(self.actions(),[])

    def test_stop_or_config_change_during_probe_cancels_recovery(self):
        for kind in ('STOP_DURING_PROBE','CHANGE_DURING_PROBE'):
            (self.root/'stopped').unlink(missing_ok=True)
            self.fail_three(EXTERNAL_OK='0',**{kind:'1'})
        self.assertEqual(self.actions(),[])

    def test_changed_config_starts_new_failure_streak(self):
        self.tick(EXTERNAL_OK='0'); self.tick(EXTERNAL_OK='0')
        (self.root/'config.json').write_text(json.dumps(self.config)+'\n')
        self.tick(EXTERNAL_OK='0')
        self.assertEqual(self.actions(),[])

    def test_recovery_cooldown_and_hourly_budget(self):
        self.fail_three(EXTERNAL_OK='0')
        for t in (1090,1120,1150,1359): self.clock(t); self.tick(EXTERNAL_OK='0')
        self.assertEqual(self.actions(),['sing-box'])
        self.clock(1360); self.tick(EXTERNAL_OK='0')
        for t in (1390,1420,1660): self.clock(t); self.tick(EXTERNAL_OK='0')
        self.assertEqual(self.actions(),['sing-box']*3)
        for t in (1690,1720,1960,4599): self.clock(t); self.tick(EXTERNAL_OK='0')
        self.assertEqual(self.actions(),['sing-box']*3)
        self.clock(4600); self.tick(EXTERNAL_OK='0')
        self.assertEqual(self.actions(),['sing-box']*4)

    def test_invalid_config_or_recovery_state_cannot_restart_service(self):
        self.tick(EXTERNAL_OK='0',CONFIG_OK='0')
        self.tick(EXTERNAL_OK='0',CONFIG_OK='0')
        p=subprocess.run(['sh',str(self.script),'tick'],
                         env=dict(self.env,EXTERNAL_OK='0',CONFIG_OK='0'),capture_output=True)
        self.assertNotEqual(p.returncode,0)
        self.assertEqual(self.actions(),[])
        for bad in ('', '[0,0,0]', '[0,4,0,3]', '[999999,0,0,3]', '[-1,0,0,3]', '[0,0.1,0,3]'):
            (self.root/'ufi-dns-health.state').write_text(bad)
            p=subprocess.run(['sh',str(self.script),'tick'],env=self.env,capture_output=True)
            self.assertNotEqual(p.returncode,0,bad)
        self.assertEqual(self.actions(),[])

    def test_dns_probe_rejects_zero_exit_servfail_refused_empty_and_timeout(self):
        for mode in ('SERVFAIL','REFUSED','empty','timeout','ok'):
            p=subprocess.run(['sh',str(LIB/'ufi_dns_tunnel.sh'),'probe-local'],
                             env=dict(self.env,DIG_MODE=mode),capture_output=True)
            self.assertEqual(p.returncode==0,mode=='ok',mode)

    def test_bootstrap_transport_is_tcp_by_default_and_udp_remains_explicit(self):
        for transport in ('tcp','udp','invalid'):
            p=subprocess.run(['sh',str(LIB/'ufi_dns_tunnel.sh'),'filter','main'],
                             input=json.dumps(self.config),text=True,capture_output=True,
                             env=dict(self.env,TRANSPORT=transport))
            self.assertEqual(p.returncode==0,transport!='invalid',p.stderr)
            if p.returncode==0:
                out=json.loads(p.stdout)
                self.assertEqual(out['dns']['servers'][1]['type'],transport)
                self.assertEqual(out['dns']['servers'][1]['server'],'77.88.8.8')
                self.assertEqual(out['outbounds'][0]['server'],'proxy.example')
                self.assertEqual(out['dns']['servers'][0]['detour'],'main-out')

    def test_ui_dns_check_rejects_servfail_even_when_dig_exits_zero(self):
        function=PODKOP.read_text().split('dns_query_ok() {',1)[1].split('\ncheck_dns_available()',1)[0]
        code='dns_query_ok() {'+function+'\ndns_query_ok @127.0.0.1 example.com A\n'
        for mode in ('SERVFAIL','REFUSED','empty','timeout','ok'):
            p=subprocess.run(['sh','-c',code],env=dict(self.env,DIG_MODE=mode),capture_output=True)
            self.assertEqual(p.returncode==0,mode=='ok',mode)


if __name__=='__main__': unittest.main()
