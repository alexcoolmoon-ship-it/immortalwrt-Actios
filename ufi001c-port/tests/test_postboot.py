import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'migration' / 'ufi-postboot-check.sh'
TARGET = 'eutran-1|eutran-3|eutran-5|eutran-8|eutran-38|eutran-39|eutran-40|eutran-41'
SECRET = 'DO_NOT_EXPORT_UUID_IMSI_APN_PASSWORD'
FAKE = r'''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['TEST_CASE'])
s = json.loads((p/'state.json').read_text())
name = Path(sys.argv[0]).name
a = sys.argv[1:]
s['calls'].append([name] + a)
mode = s['mode']
out = ''; rc = 0
secret = 'DO_NOT_EXPORT_UUID_IMSI_APN_PASSWORD'
if name == 'mmcli':
    assert a == ['--modem=qcom-soc','--timeout=12','--output-json'], a
    if mode == 'mm_unavailable': rc = 1
    else:
        bands = s['target'].split('|') if mode != 'different' else ['eutran-1','eutran-3']
        out = json.dumps({'modem':{'generic':{'state':'connected','revision':'UFI001BC 20211121',
           'hardware-revision':'10000','current-bands':bands,'current-modes':'allowed: 4g; preferred: none',
           'supported-bands':bands, 'equipment-identifier':secret, 'device-identifier':secret},
           '3gpp':{'registration-state':'home','imei':secret,'operator-name':secret},'sim':secret}})
elif name == 'id':
    assert a == ['-u'], a
    out = '0'
elif name == 'uci':
    assert len(a)==3 and a[:2]==['-q','get'], 'UCI mutation forbidden: '+repr(a)
    vals = {'network.modem.allowedmode':'4g','network.modem.allowedbands':s['target'],
      'network.modem.auto':'1','network.modem.autoconnect':'1','network.modem.defaultroute':'1',
      'podkop.settings.config_path':'/tmp/'+secret+'.json',
      'podkop.settings.cloudflare_dns_patch':'1' if mode.startswith('clean_cloudflare') else '0'}
    out = vals.get(a[2], ''); rc = 0 if a[2] in vals else 1
elif name == 'ubus':
    assert a==['-t','5','call','network.interface.modem','status'], a
    out = json.dumps({'up':True,'pending':False,'available':True,'uptime':40,
      'proto':'modemmanager','device':'wwan0','l3_device':'wwan0','data':{'apn':secret}})
elif name == 'ip':
    if mode.startswith('clean') and a==['-j','link','show','dev','wwan0']:
        out=json.dumps([{'flags':['UP','LOWER_UP']}])
    elif mode.startswith('clean') and a==['-j','-4','address','show','dev','wwan0']:
        out=json.dumps([{'addr_info':[{'family':'inet','scope':'global','local':'192.0.2.5'}]}])
    elif mode.startswith('clean') and a==['-4','route','get','77.88.8.8']:
        out='77.88.8.8 via 192.0.2.6 dev wwan0'
    elif mode.startswith('clean') and a==['-4','route','get','198.18.0.254','mark','0x100000']:
        out='local 198.18.0.254 dev lo table podkop mark 0x100000'
    else:
        assert a in [['-4','address','show'],['-4','route','show','table','main']], a
        out = 'wwan0 inet 192.0.2.5/30' if a[1]=='address' else 'default via 192.0.2.6 dev wwan0'
elif name == 'curl':
    assert '--noproxy' in a and a[a.index('--noproxy')+1]=='', a
    assert a[a.index('--proxy')+1]=='http://127.0.0.1:4534', a
    assert '--max-time' in a and a[a.index('--max-time')+1]=='12', a
    body = {'Status':0,'Answer':[{'type':1,'data':'93.184.215.14'}], 'secret':secret}
    if mode=='invalid_doh': body['Answer'][0]['data']='999.0.0.1'
    if mode=='http504': rc=22; out='504'
    else: out='200'
    Path(a[a.index('-o')+1]).write_text(json.dumps(body))
elif name == 'dig':
    if mode.startswith('clean') and a[:3]==['@77.88.8.8','example.com','A']:
        assert '+tcp' in a and '+answer' in a
        out=';; status: SERVFAIL,' if mode=='clean_dns_fail' else ';; status: NOERROR,\nexample.com. 60 IN A 93.184.215.14'
    elif mode.startswith('clean') and a[1]=='fakeip.podkop.fyi':
        assert a[0] in ('@127.0.0.42','@127.0.0.1')
        out='203.0.113.3' if mode=='clean_fakeip_fail' else '198.18.0.1'
    else:
        assert a==['@127.0.0.1','example.com','A','+time=3','+tries=1','+short'], a
        out = '' if mode=='dns_fail' else '93.184.215.14'
elif name == 'dmesg':
    out = '\n'.join('[ %.3f] rflte_old.c:100 old boot event' % (i/10) for i in range(190))
    if mode=='new_rf': out+='\n[ 110.500] remoteproc remoteproc0: crash detected'
elif name == 'sleep': pass
elif name == 'podkop':
    assert a == ['cloudflare_check'], a
    rc = 1 if mode == 'clean_cloudflare_fail' else 0
elif name == 'sing-box':
    assert a==['version'] or a==['check','-c','/tmp/'+secret+'.json'], a
    if a==['version']: out='sing-box version 1.12.test'
elif name == 'fake-boot':
    s['boot_calls']=s.get('boot_calls',0)+1
    out='boot-two' if mode=='boot_changed' and s['boot_calls']>1 else 'boot-one'
else: raise AssertionError('Unexpected command: '+repr([name]+a))
(p/'state.json').write_text(json.dumps(s))
if out: print(out)
sys.exit(rc)
'''
HARNESS = r'''
. "$TEST_SCRIPT"
ROOT="$TEST_ROOT"
PREVIOUS="$TEST_CASE/no-previous-run"
read_board() { echo thwc,ufi001c; }
read_arch() { echo aarch64; }
read_boot() { fake-boot; }
mono() { echo 100; }
precise_mono() { echo 100.000; }
health() { echo 'LTE: kernel link=1 address=1 default_route=1 (wwan0)'; [ "$TEST_MODE" != health_fail ]; }
reboot_evidence() {
    dmesg | rf_filter >"$RUN/report/kernel-events.txt"
    echo 'No readable pstore record' >"$RUN/report/pstore-events.txt"
}
collect "$TEST_TOKEN"
'''

class PostbootCheck(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.p=Path(self.temp.name); self.bin=self.p/'bin'; self.bin.mkdir()
        self.root=self.p/'reports'
        self.fake=self.p/'fake.py'; self.fake.write_text(FAKE); self.fake.chmod(0o755)
        for name in ['mmcli','id','uci','ubus','ip','curl','dig','dmesg','sleep','sing-box','fake-boot','podkop']:
            (self.bin/name).symlink_to(self.fake)
        self.env=dict(os.environ, TEST_CASE=str(self.p),TEST_ROOT=str(self.root),TEST_SCRIPT=str(SCRIPT),
          UFI_POSTBOOT_SOURCE_ONLY='1', PATH=str(self.bin)+os.pathsep+os.environ['PATH'])

    def run_check(self, mode='ok', token='test-run'):
        (self.p/'state.json').write_text(json.dumps({'mode':mode,'target':TARGET,'calls':[]}))
        env=dict(self.env,TEST_MODE=mode,TEST_TOKEN=token)
        harness=HARNESS
        if mode.startswith('clean'):
            harness=harness.replace('health() { echo \'LTE: kernel link=1 address=1 default_route=1 (wwan0)\'; [ "$TEST_MODE" != health_fail ]; }','')
            harness=harness.replace('collect "$TEST_TOKEN"','clean_profile() { return 0; }; collect "$TEST_TOKEN"')
        p=subprocess.run(['sh','-c',harness],env=env,capture_output=True,text=True,timeout=20)
        return p

    def test_clean_profile_checks_real_link_dns_and_fakeip_without_removed_helpers(self):
        p=self.run_check('clean_ok')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('CURRENT_BOOT_LTE_DNS_FAKEIP_OK',self.summary())
        self.assertIn('Local FakeIP and marked route: 3/3',self.summary())
        calls=json.loads((self.p/'state.json').read_text())['calls']
        self.assertFalse(any(c[0]=='curl' for c in calls))
        self.assertFalse(any(c[0]=='sing-box' and 'check' in c for c in calls))

    def test_clean_profile_cannot_pass_with_wrong_fakeip_or_upstream_servfail(self):
        for mode in ('clean_fakeip_fail','clean_dns_fail'):
            p=self.run_check(mode,mode)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            self.assertNotIn('\nCURRENT_BOOT_LTE_DNS_FAKEIP_OK\n',self.summary(mode))

    def test_clean_cloudflare_checks_running_tunnel_instead_of_plain_upstream(self):
        for mode in ('clean_cloudflare_ok','clean_cloudflare_fail'):
            p=self.run_check(mode,mode)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            calls=json.loads((self.p/'state.json').read_text())['calls']
            self.assertEqual(sum(c==['podkop','cloudflare_check'] for c in calls),3)
            self.assertFalse(any(c[:2]==['dig','@77.88.8.8'] for c in calls))
            self.assertIn('Cloudflare DoH via proxy: '+('3' if mode.endswith('_ok') else '0')+'/3', self.summary(mode))
            if mode.endswith('_ok'):
                self.assertIn('\nCURRENT_BOOT_LTE_CLOUDFLARE_FAKEIP_OK\n',self.summary(mode))
            else:
                self.assertIn('\nLTE_UP_DNS_OR_PROXY_UNSTABLE\n',self.summary(mode))

    def test_clean_version_detection_accepts_both_clean_releases(self):
        source=SCRIPT.read_text().replace('/etc/ufi001c-release',str(self.p/'release'))
        for release, expected in (('UFI001C v3.2.1 Clean / OpenWrt 25.12.5',0),
                                  ('UFI001C v3.2.2 Clean / OpenWrt 25.12.5',0),
                                  ('UFI001C v3.2.0 Final Candidate / OpenWrt 25.12.5',1)):
            (self.p/'release').write_text(release+'\n')
            p=subprocess.run(['sh','-c',source+'\nclean_profile'],env=self.env,capture_output=True,text=True)
            self.assertEqual(p.returncode,expected,p.stderr)

    def summary(self, token='test-run'):
        return (self.root/f'run-{token}'/'report'/'SUMMARY.txt').read_text()

    def test_success_is_current_and_does_not_export_credentials(self):
        p=self.run_check(); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        text=self.summary()
        self.assertIn('\nCURRENT_BOOT_LTE_DNS_PROXY_OK\n',text)
        self.assertIn('LTE health: 3/3\nProxy DoH: 3/3\nRouter DNS: 3/3',text)
        archive=self.root/'ufi-postboot-report-test-run.tar.gz'
        with tarfile.open(archive) as t:
            members=t.getmembers()
            self.assertTrue(all(m.name=='report' or m.name.startswith('report/') for m in members))
            raw=b''.join(t.extractfile(m).read() for m in members if m.isfile())
        self.assertNotIn(SECRET.encode(),raw)
        self.assertNotIn(b'network.backup',raw)
        calls=json.loads((self.p/'state.json').read_text())['calls']
        self.assertEqual(sum(c[0]=='curl' for c in calls),3)
        self.assertEqual(sum(c[0]=='dig' for c in calls),3)
        self.assertTrue(all(c[1:3]==['-q','get'] for c in calls if c[0]=='uci'))
        self.assertTrue(all('--disable' not in c and '--enable' not in c for c in calls))

    def test_proxy_http_error_invalid_answer_and_dns_failure_never_pass(self):
        for mode in ['http504','invalid_doh','dns_fail']:
            with self.subTest(mode=mode):
                p=self.run_check(mode,mode); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertIn('\nLTE_UP_DNS_OR_PROXY_UNSTABLE\n',self.summary(mode))

    def test_health_failure_never_passes_on_proxy_success(self):
        p=self.run_check('health_fail'); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('\nLTE_NOT_CONFIRMED\n',self.summary())

    def test_different_mask_is_explicit(self):
        p=self.run_check('different'); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('\nLTE_UP_PROFILE_DIFFERS\n',self.summary())

    def test_unavailable_modem_is_explicit(self):
        p=self.run_check('mm_unavailable'); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('\nMODEM_STATUS_UNAVAILABLE\n',self.summary())

    def test_new_rf_event_is_seen_even_after_more_than_160_old_events(self):
        p=self.run_check('new_rf'); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('\nNEW_KERNEL_EVENTS_REVIEW_REQUIRED\n',self.summary())
        self.assertIn('110.500',(self.root/'run-test-run/report/new-kernel-events.txt').read_text())

    def test_boot_change_cannot_return_current_boot_success(self):
        p=self.run_check('boot_changed'); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('\nBOOT_CHANGED_DURING_CHECK\n',self.summary())

    def test_new_token_creates_fresh_report_reused_token_is_refused(self):
        p=self.run_check(); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        old=(self.root/'ufi-postboot-report-test-run.tar.gz').read_bytes()
        p=self.run_check('http504','second'); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('Proxy DoH: 0/3',self.summary('second'))
        p=self.run_check('http504'); self.assertNotEqual(p.returncode,0)
        self.assertEqual((self.root/'ufi-postboot-report-test-run.tar.gz').read_bytes(),old)
        p=self.run_check('ok','../../bad'); self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.p/'bad').exists())

if __name__=='__main__': unittest.main(verbosity=2)
