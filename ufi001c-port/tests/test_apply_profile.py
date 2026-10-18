"""Execute the profile worker with all modem/network mutations simulated.

The production shell and jq logic run under native BusyBox ash. No host radio,
network service, configuration or process is changed.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'live-fix/ufi-apply-profile.sh'
BUSYBOX = Path(os.environ['UFI_TEST_BUSYBOX']) if os.environ.get('UFI_TEST_BUSYBOX') else None
TARGET = 'eutran-1|eutran-3|eutran-5|eutran-8|eutran-38|eutran-39|eutran-40|eutran-41'
SAFE = 'eutran-1|eutran-3'
STATE = {'now':200.0, 'up':True, 'ifups':0, 'restarts':0, 'curl':0, 'mode':'normal',
         'writes':[], 'calls':[], 'firmware':'UFI001BC 20211121  1  [Nov 04 2016 02:00:00]',
         'hardware':'10000', 'hash':'fcb4f10049158ac687dd621ac0a6fd0f431e8ddebfd4aad2a6a86222737cfb3e',
         'bands':[f'eutran-{b}' for b in [1,3,5,7,8,20,38,39,40,41]],
         'current':['eutran-1','eutran-3','eutran-7'],
         'cfg':{'network.modem':'interface','network.modem.proto':'modemmanager',
                'network.modem.device':'qcom-soc','network.modem.allowedmode':'4g',
                'network.modem.auto':'1','network.modem.autoconnect':'1',
                'network.modem.allowedbands':'eutran-1|eutran-3|eutran-7'}}

FAKE = r'''import json,os,sys
from pathlib import Path
p=Path(os.environ['CASE']); s=json.loads((p/'state.json').read_text())
name=Path(sys.argv[0]).name; a=sys.argv[1:]; code=0; out=''
s['calls'].append([name]+a); s['now']+=0.05
if name=='id': out='0'
elif name=='uname': out='aarch64'
elif name=='sleep': s['now']+=float(a[0])
elif name=='sha256sum': out=s['hash']+'  proto.sh'
elif name=='uci':
 args=[x for x in a if x!='-q']; cmd=args[0]; v=args[1] if len(args)>1 else ''
 if cmd=='changes': out=s.get('pending','')
 elif cmd=='get': out=s['cfg'].get(v,''); code=0 if v in s['cfg'] else 1
 elif cmd=='set':
  k,v=v.split('=',1); s['cfg'][k]=v; s['writes'].append([cmd,k,v])
 elif cmd=='delete':
  code=0 if v in s['cfg'] else 1; s['cfg'].pop(v,None); s['writes'].append([cmd,v])
 elif cmd=='commit': s['writes'].append([cmd,v])
 else: raise RuntimeError(a)
elif name=='ufi-lte-health': code=0 if s['up'] else 1
elif name=='ubus': out=json.dumps({'up':s['up'],'pending':False})
elif name=='mmcli':
 if '--disable' in a: s['up']=False; s['writes'].append(['mm-disable'])
 elif '--output-json' in a:
  out=json.dumps({'modem':{'generic':{'state':'connected' if s['up'] else 'disabled',
    'revision':s['firmware'],'hardware-revision':s['hardware'],
    'supported-bands':s['bands'],'current-bands':s['current'],
    'current-modes':'allowed: 4g; preferred: none'}}})
 else: raise RuntimeError(a)
elif name=='ifdown': s['up']=False; s['writes'].append([name]+a)
elif name=='ifup':
 s['ifups']+=1; s['writes'].append([name]+a)
 s['up']=not (s['mode']=='no_lte' and s['ifups']==1)
 s['current']=s['cfg']['network.modem.allowedbands'].split('|')
 if s['mode']=='rf' and s['ifups']==1: s['rfstamp']=s['now']
elif name=='dmesg':
 out='[ 90.000000] remoteproc remoteproc0: handling crash #1'
 if s.get('rfstamp'):
  out+='\n[ %.6f] qcom-q6v5-mss: fatal error received: rflte_new.c:123: simulated RF failure'%s['rfstamp']
elif name=='curl':
 s['curl']+=1
 proxy=a[a.index('--proxy')+1] if '--proxy' in a else ''
 if proxy!='http://127.0.0.1:4534' or '--noproxy' not in a: raise RuntimeError('direct fallback')
 fail=s['mode']=='proxy_fail' or (s['mode']=='transient' and s['curl']==1)
 if s['mode']=='mixed': fail=s['curl']==3
 if s['mode']=='restart_needed': fail=s['restarts']==0
 if fail: code=22; out='504'; body='<html>temporary upstream error</html>'
 else: out='200'; body=json.dumps({'Status':0,'Answer':[{'type':1,'data':'93.184.215.14'}]})
 Path(a[a.index('-o')+1]).write_text(body)
elif name=='dig':
 if a[0]!='@127.0.0.1': raise RuntimeError('direct DNS fallback')
 out='93.184.215.14'; code=0
elif name=='sing-box': code=1 if s['mode']=='bad_config' else 0
elif name=='sing-box-init':
 if a!=['restart']: raise RuntimeError(a)
 s['restarts']+=1; s['writes'].append(['engine-restart'])
elif name=='ufi-services-check':
 code=1 if s['mode']=='package_fail' else 0
 out='GitHub via service proxy: '+('PASS' if code==0 else 'FAIL')
elif name=='ip': raise RuntimeError('no manual route writes expected')
else: raise RuntimeError(name)
(p/'state.json').write_text(json.dumps(s)); (p/'uptime').write_text('%.2f 0.0\n'%s['now'])
if out: print(out)
sys.exit(code)
'''


class ApplyProfile(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.p=Path(self.temp.name); self.bin=self.p/'bin'; self.bin.mkdir()
        self.state=copy.deepcopy(STATE)
        (self.p/'fake.py').write_text('#!/usr/bin/python3\n'+FAKE)
        (self.p/'fake.py').chmod(0o755)
        for name in ['id','uname','sleep','sha256sum','uci','ufi-lte-health','ubus','mmcli',
                     'ifdown','ifup','dmesg','curl','dig','sing-box','sing-box-init','ufi-services-check','ip']:
            (self.bin/name).symlink_to(self.p/'fake.py')
        # Simulate the device jq capability boundary, retaining real JSON logic.
        jq=self.bin/'jq'
        jq.write_bytes((ROOT/'tests/fixtures/jq-no-regex.py').read_bytes()); jq.chmod(0o755)
        self.env=dict(os.environ,CASE=str(self.p),UFI_PROFILE_SOURCE_ONLY='1',
                      UFI_TEST_REAL_JQ=shutil.which('jq'),PATH=str(self.bin)+os.pathsep+os.environ['PATH'])
        (self.p/'board').write_text('thwc,ufi001c\n')
        (self.p/'boot').write_text('test-boot\n')
        (self.p/'network').write_text('PRIVATE_PASSWORD_DO_NOT_EXPORT\n')
        ready=ROOT/'overlay/package/openstick/ufi-service-proxy/files/ready.jq'
        config={'inbounds':[{'tag':'service-mixed-in','type':'mixed','listen':'127.0.0.1','listen_port':4534}],
                'outbounds':[{'type':'vless','tag':'test-proxy','server':'example.invalid','uuid':'PRIVATE_UUID'}],
                'route':{'rules':[{'inbound':['service-mixed-in'],'outbound':'test-proxy'}]}}
        (self.p/'config.json').write_text(json.dumps(config))
        self.state['cfg']['podkop.settings.config_path']=str(self.p/'config.json')
        self.root=self.p/'data'; self.run=self.root/'run-test'; (self.run/'report').mkdir(parents=True)
        (self.run/'result').write_text('IN_PROGRESS\n'); (self.run/'boot-id').write_text('test-boot\n')
        self.save()
        code=SOURCE.read_text()
        for old,new in {
            'ROOT=/root/ufi-profile':'ROOT='+str(self.root),
            'LOCK=/tmp/ufi-profile.lock':'LOCK='+str(self.p/'lock'),
            '/tmp/sysinfo/board_name':str(self.p/'board'),
            '/proc/uptime':str(self.p/'uptime'),
            '/proc/sys/kernel/random/boot_id':str(self.p/'boot'),
            '/etc/config/network':str(self.p/'network'),
            '/usr/sbin/ufi-lte-health':str(self.bin/'ufi-lte-health'),
            '/usr/sbin/ufi-services-check':str(self.bin/'ufi-services-check'),
            '/etc/init.d/sing-box':str(self.bin/'sing-box-init'),
            '/usr/share/ufi-service-proxy/ready.jq':str(ready),
            '/tmp/ufi-diagnose.lock':str(self.p/'diag-lock'),
            '/tmp/ufi-finish.lock':str(self.p/'finish-lock'),
            '/tmp/ufi-proxy-recover.lock':str(self.p/'recover-lock'),
        }.items(): code=code.replace(old,new)
        self.script=self.p/'apply.sh'; self.script.write_text(code)

    def save(self):
        (self.p/'state.json').write_text(json.dumps(self.state))
        (self.p/'uptime').write_text('%.2f 0.0\n'%self.state['now'])

    def load(self): return json.loads((self.p/'state.json').read_text())

    def call(self,command='worker'):
        return subprocess.run((([str(BUSYBOX),'ash'] if BUSYBOX else ['sh'])+['-c',
            '. "$CASE/apply.sh"; RUN="$CASE/data/run-test"; '+command]),
            env=self.env,capture_output=True,text=True,timeout=25)

    def outcome(self): return (self.run/'result').read_text().strip()

    def test_profile_keeps_all_but_the_two_reproduced_failures(self):
        p=self.call(); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(self.outcome(),'PROFILE_APPLIED_CHECKS_OK')
        state=self.load()
        self.assertEqual(state['cfg']['network.modem.allowedbands'],TARGET)
        self.assertEqual(state['cfg']['network.modem.allowedmode'],'4g')
        self.assertEqual(state['ifups'],1)
        self.assertEqual(state['restarts'],0)
        self.assertTrue(all(w[1]=='modem' for w in state['writes'] if w[0] in ['ifdown','ifup']))
        self.assertTrue(all(w[1].startswith('network.modem.') for w in state['writes'] if w[0] in ['set','delete']))
        self.assertTrue((self.run/'network.backup.private').exists())

    def test_rebooted_wait_returns_a_distinct_failure_instead_of_old_success(self):
        p=self.call('boot() { echo another-boot; }; wait_run')
        self.assertEqual(p.returncode,3,p.stdout+p.stderr)
        self.assertEqual(self.outcome(),'REBOOT_INTERRUPTED_VERIFY_LTE')
        self.assertIn('CHECK-LTE.cmd',p.stdout)
        self.assertNotIn('PROFILE_APPLIED_CHECKS_OK',p.stdout)

    def test_finished_old_run_is_not_presented_as_a_check_of_a_new_boot(self):
        (self.run/'DONE').touch()
        (self.run/'result').write_text('PROFILE_APPLIED_CHECKS_OK\n')
        p=self.call('boot() { echo another-boot; }; wait_run')
        self.assertEqual(p.returncode,3,p.stdout+p.stderr)
        self.assertEqual(self.outcome(),'REBOOT_INTERRUPTED_VERIFY_LTE')

    def test_connection_failure_uses_b1_b3_not_the_old_crashing_profile(self):
        self.state['mode']='no_lte'; self.save(); p=self.call()
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(self.outcome(),'FALLBACK_B1_B3')
        self.assertEqual(self.load()['cfg']['network.modem.allowedbands'],SAFE)
        self.assertEqual(self.load()['ifups'],2)

    def test_new_rf_assert_causes_fallback_and_evidence_is_preserved(self):
        self.state['mode']='rf'; self.save(); p=self.call()
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(self.outcome(),'FALLBACK_B1_B3')
        self.assertIn('simulated RF failure',(self.run/'report/new-rf-events.txt').read_text())

    def test_transient_http_504_is_retried_without_restarting_radio_or_engine(self):
        self.state['mode']='transient'; self.save(); p=self.call()
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(self.outcome(),'PROFILE_APPLIED_CHECKS_OK')
        self.assertIn('PROXY_DOH_HTTP_504',p.stdout)
        self.assertEqual(self.load()['ifups'],1); self.assertEqual(self.load()['restarts'],0)
        self.assertEqual(self.load()['curl'],4)

    def test_three_failed_probes_permit_only_one_engine_restart(self):
        for mode,outcome in [('restart_needed','PROFILE_APPLIED_CHECKS_OK'),
                             ('proxy_fail','PROFILE_APPLIED_PROXY_UNSTABLE')]:
            with self.subTest(mode=mode):
                self.state=copy.deepcopy(STATE)
                self.state['cfg']['podkop.settings.config_path']=str(self.p/'config.json')
                self.state['mode']=mode; self.save()
                p=self.call(); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertEqual(self.outcome(),outcome)
                self.assertEqual(self.load()['restarts'],1); self.assertEqual(self.load()['ifups'],1)
                self.assertEqual(self.load()['cfg']['network.modem.allowedbands'],TARGET)

    def test_one_failure_after_two_successes_does_not_restart_engine(self):
        self.state['mode']='mixed'; self.save(); p=self.call()
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(self.outcome(),'PROFILE_APPLIED_CHECKS_OK')
        self.assertEqual(self.load()['restarts'],0); self.assertEqual(self.load()['curl'],6)

    def test_bad_config_is_never_started_and_packages_failure_is_not_a_radio_fault(self):
        for mode in ['bad_config','package_fail']:
            with self.subTest(mode=mode):
                self.state=copy.deepcopy(STATE)
                self.state['cfg']['podkop.settings.config_path']=str(self.p/'config.json')
                self.state['mode']=mode; self.save(); p=self.call()
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertEqual(self.outcome(),'PROFILE_APPLIED_PROXY_UNSTABLE')
                self.assertEqual(self.load()['ifups'],1); self.assertEqual(self.load()['restarts'],0)

    def test_preflight_refuses_other_hardware_protocol_and_unsaved_config(self):
        variants=[{'hardware':'20000'},{'firmware':'ANOTHER 20211121'},{'hash':'unknown'},
                  {'bands':['eutran-1','eutran-3']},{'pending':"network.lan.ipaddr='192.168.8.1'"}]
        for updates in variants:
            with self.subTest(updates=updates):
                self.state=copy.deepcopy(STATE); self.state.update(updates); self.save()
                p=self.call('preflight'); self.assertNotEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertEqual(self.load()['writes'],[])

    def test_report_never_contains_full_network_or_proxy_credentials(self):
        p=self.call(); self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        with tarfile.open(self.root/'ufi-profile-report.tar.gz') as t:
            raw=b''.join(t.extractfile(m).read() for m in t if m.isfile())
            self.assertNotIn(b'PRIVATE_PASSWORD',raw); self.assertNotIn(b'PRIVATE_UUID',raw)
            self.assertTrue(all(m.name=='report' or m.name.startswith('report/') for m in t))


if __name__=='__main__': unittest.main(verbosity=2)
