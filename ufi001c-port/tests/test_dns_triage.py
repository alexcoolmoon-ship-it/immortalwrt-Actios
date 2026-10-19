"""Execute the complete read-only collector with isolated network command mocks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'dns-triage/check.sh'
PRIVATE = 'PRIVATE_UUID_URL_APN_PASSWORD'
FAKE = r'''import json,os,sys
from pathlib import Path
p=Path(os.environ['CASE']);mode=os.environ['MODE']
name=Path(sys.argv[0]).name;a=sys.argv[1:]
with (p/'calls.jsonl').open('a') as f:f.write(json.dumps([name]+a)+'\n')
out='';rc=0
if name=='id':
 assert a==['-u'];out='0'
elif name=='uci':
 assert a[:2]==['-q','get'] and len(a)==3, a
 assert a[2] in ('podkop.settings.config_path','podkop.settings.cloudflare_dns_patch')
 out=str(p/'config.json') if a[2].endswith('config_path') else '1'
elif name=='ubus':
 assert a==['-t','5','call','network.interface.modem','status'],a
 out=json.dumps(dict(up=mode!='lte_down',pending=False,available=True,proto='modemmanager',l3_device='' if mode=='lte_down' else 'wwan0',data={'apn':'PRIVATE_UUID_URL_APN_PASSWORD'}))
elif name=='ip':
 if a==['-j','link','show','dev','wwan0']:out=json.dumps([{'flags':['UP']}])
 elif a==['-j','-4','address','show','dev','wwan0']:out=json.dumps([{'addr_info':[{'family':'inet','scope':'global'}]}])
 elif a==['-4','route','get','77.88.8.8']:
  if mode=='lte_down':rc=2
  else:out='77.88.8.8 dev wwan0 src 192.0.2.10'
 else:raise AssertionError('Unexpected/mutating ip: '+repr(a))
elif name=='pgrep':
 assert a==['-x','sing-box'];out='1234'
elif name=='dig':
 assert a[0] in ('@77.88.8.8','@127.0.0.42','@127.0.0.1'),a
 assert '+tries=1' in a and '+time=2' in a
 if '+tcp' not in a:assert '+notcp' in a and '+ignore' in a
 if a[0]=='@77.88.8.8' and (mode=='lte_down' or (mode=='tcp_blocked' and '+tcp' in a)):rc=9
 else:
  answer='198.18.0.7' if a[1]=='fakeip.podkop.fyi' else '203.0.113.7'
  out=';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n'+a[1]+'. 60 IN A '+answer
elif name=='curl':
 assert a[a.index('--proxy')+1]=='http://127.0.0.1:4534',a
 assert a[a.index('--noproxy')+1]=='' and a[-1]=='https://1.1.1.1/dns-query',a
 assert '--insecure' not in a and '-k' not in a
 assert '--connect-timeout' in a and '--max-time' in a and '--max-filesize' in a
 dest=Path(a[a.index('-o')+1])
 if mode in ('tcp_blocked','tls_error','lte_down'):
  rc=60 if mode=='tls_error' else 28
  sys.stderr.write('curl: (28) Proxy CONNECT timed out' if rc==28 else 'curl: (60) SSL certificate problem: certificate has expired')
  dest.write_bytes(b'')
 else:
  q=Path(a[a.index('--data-binary')+1][1:]).read_bytes()
  body=q[:2]+bytes.fromhex('81820001000000000000' if mode=='servfail' else '81800001000100000000')+q[12:]
  if mode!='servfail':body+=bytes.fromhex('c00c000100010000003c0004cb007107')
  dest.write_bytes(body)
 out='CURL_HTTP='+('000' if rc else '200')+'\nCURL_CONNECT_HTTP=200\nCURL_CONNECT_SECONDS=0.01\nCURL_TOTAL_SECONDS=0.02'
elif name=='dmesg':
 out='\n'.join('[1.%03d] rflte_old' % i for i in range(35))+'\n[100.123] Out of memory: Killed process 1234 (sing-box)'
elif name=='logread':
 assert a==['-e','sing-box']
 out='dns: lookup PRIVATE_UUID_URL_APN_PASSWORD failed\nx509 certificate PRIVATE_UUID_URL_APN_PASSWORD\ntimeout PRIVATE_UUID_URL_APN_PASSWORD'
else:raise AssertionError('Unexpected command: '+name)
if out:print(out)
sys.exit(rc)
'''


class DNSTriageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.bin=self.root/'bin';self.bin.mkdir()
        script=SCRIPT.read_text()
        fixtures={'/tmp/sysinfo/board_name':('board','thwc,ufi001c\n'),
                  '/etc/ufi001c-release':('release','UFI001C v3.2.1 Clean / OpenWrt 25.12.5\n'),
                  '/proc/sys/kernel/random/boot_id':('boot','boot-before-reboot\n'),
                  '/proc/uptime':('uptime','900.0 10.0\n'),
                  '/proc/meminfo':('meminfo','MemTotal: 512000 kB\nMemAvailable: 32000 kB\n')}
        for path,(name,data) in fixtures.items():
            (self.root/name).write_text(data);script=script.replace(path,str(self.root/name))
        script=script.replace('/tmp/ufi-dns-triage.',str(self.root/'probe.'))
        self.script=self.root/'check.sh';self.script.write_text(script)
        for name in ('id','uci','ubus','ip','pgrep','dig','curl','dmesg','logread'):
            p=self.bin/name;p.write_text('#!'+sys.executable+'\n'+FAKE);p.chmod(0o755)
        self.config={'dns':{'servers':[{'tag':'bootstrap-dns-server','type':'tcp','server':'77.88.8.8'},
            {'tag':'dns-server','type':'https','server':'1.1.1.1','path':'/dns-query','detour':'main-out'},
            {'tag':PRIVATE,'type':'https','server':PRIVATE}]},
            'outbounds':[{'tag':'main-out','type':'vless','server':'private-proxy.example',
                'uuid':PRIVATE,'tls':{'server_name':PRIVATE},'domain_resolver':{'server':'bootstrap-dns-server'}}],
            'inbounds':[{'tag':'service-mixed-in','listen':'127.0.0.1','listen_port':4534}],
            'route':{'rules':[{'inbound':['service-mixed-in'],'outbound':'main-out'}]},
            'experimental':{'cache_file':{'store_fakeip':True}}}

    def run_collector(self,mode='ok'):
        data=json.dumps(self.config).encode();cfg=self.root/'config.json';cfg.write_bytes(data)
        p=subprocess.run(['sh',str(self.script)],env=dict(os.environ,CASE=str(self.root),MODE=mode,
            PATH=str(self.bin)+os.pathsep+os.environ['PATH']),capture_output=True,text=True,timeout=10)
        self.assertEqual(cfg.read_bytes(),data)
        self.assertFalse(list(self.root.glob('probe.*')))
        calls=[json.loads(x) for x in (self.root/'calls.jsonl').read_text().splitlines()]
        self.assertNotIn(PRIVATE,p.stdout+p.stderr)
        self.assertNotIn('private-proxy.example',p.stdout+p.stderr)
        self.assertNotIn('203.0.113.7',p.stdout+p.stderr)
        return p,calls

    def test_success_preserves_settings_and_hides_proxy_secrets(self):
        p,calls=self.run_collector()
        self.assertEqual(p.returncode,0,p.stderr)
        for value in ('CLOUDFLARE_DOH_PROXY=PASS','PROXY_1_YANDEX_TCP=PASS','FAKEIP_127.0.0.42=PASS',
                      'FAKEIP_127.0.0.1=PASS','"link_up":true','"ipv4":true','UFI_DNS_TRIAGE_COMPLETE',
                      'Out of memory: Killed process 1234 (sing-box)','LOG_DNS=1 LOG_TLS=1 LOG_TIMEOUT=1'):
            self.assertIn(value,p.stdout)
        self.assertEqual(sum(c[0]=='curl' for c in calls),1)

    def test_tcp_block_is_distinguished_from_working_udp_and_local_cache(self):
        p,calls=self.run_collector('tcp_blocked')
        self.assertEqual(p.returncode,0,p.stderr)
        for value in ('YANDEX_example.com_TCP=FAIL','YANDEX_example.com_UDP=PASS',
                      'PROXY_1_YANDEX_TCP=FAIL','PROXY_1_YANDEX_UDP=PASS','LOCAL_127.0.0.1=PASS',
                      'CURL_EXIT=28','Proxy CONNECT timed out','CLOUDFLARE_DOH_PROXY=FAIL'):
            self.assertIn(value,p.stdout)

    def test_tls_failure_records_certificate_error_without_disabling_verification(self):
        p,calls=self.run_collector('tls_error')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('CURL_EXIT=60',p.stdout)
        self.assertIn('certificate has expired',p.stdout)
        self.assertIn('CLOUDFLARE_DOH_PROXY=FAIL',p.stdout)

    def test_http_success_with_dns_servfail_is_not_a_dns_pass(self):
        p,calls=self.run_collector('servfail')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('CURL_HTTP=200',p.stdout)
        self.assertIn('DNS_RCODE=2 DNS_ANSWERS=0',p.stdout)
        self.assertIn('CLOUDFLARE_DOH_PROXY=FAIL',p.stdout)

    def test_wrong_service_route_does_not_probe_an_unrelated_proxy(self):
        self.config['route']['rules'][0]['outbound']='direct-out'
        p,calls=self.run_collector()
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('CLOUDFLARE_CONFIG=NOT_MATCHING',p.stdout)
        self.assertFalse(any(c[0]=='curl' for c in calls))

    def test_lte_down_and_missing_route_are_reported_without_resetting_anything(self):
        p,calls=self.run_collector('lte_down')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('"up": false',p.stdout)
        self.assertIn('LTE_KERNEL_DEVICE=UNAVAILABLE',p.stdout)
        self.assertIn('YANDEX_ROUTE_DEVICE=UNAVAILABLE',p.stdout)
        self.assertIn('YANDEX_example.com_UDP=FAIL',p.stdout)
        self.assertIn('CLOUDFLARE_DOH_PROXY=FAIL',p.stdout)


if __name__=='__main__':unittest.main()
