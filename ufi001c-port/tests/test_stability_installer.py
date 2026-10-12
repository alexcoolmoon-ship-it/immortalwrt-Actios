"""Apply/rollback of the live USB/DNS patch in an isolated fake rootfs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import unittest
import test_setup_installer as setup_fixtures

PORT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stability_builder',PORT/'stability-hotfix/build.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class StabilityInstallerTests(unittest.TestCase):
    def setUp(self):
        # Reuse the fake UCI and relocated service fixture, not a real router.
        self.f=setup_fixtures.SetupInstallerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root
        out=self.f.tmp/'stability';builder.build(out)
        self.package=self.f.tmp/'new-package';self.package.mkdir()
        with tarfile.open(out/'ufi-stability.tar.gz') as archive:
            archive.extractall(self.package,filter='data')
        shutil.copytree(self.package/'payload',self.root,dirs_exist_ok=True)
        (self.root/'etc/ufi001c-release').write_text('UFI001C v3.1.2 Setup / OpenWrt 25.12.5\n')
        (self.root/'etc/config/gc').write_text(json.dumps({'config':'gc','config.mode':'gadget','config.auto_restart':'1'}))
        self.original=(self.root/'etc/config/gc').read_bytes()
        self.f.executable(self.f.bin/'hotplug-service','#!/bin/sh\necho "$*" >> "$FIXTURE_ROOT/calls"\n[ "$1" != enabled ]\n')
        # Test-only relocation; all paths/services remain under the fake root.
        for name in ('install.sh','restore.sh'):
            path=self.package/name;code=path.read_text()
            code=code.replace('/etc/init.d/hotplug-gcreboot','hotplug-service')
            for prefix in ('/etc/', '/root/', '/tmp/sysinfo/'):
                code=code.replace(prefix,str(self.root)+prefix)
            code=code.replace('"/$path','"'+str(self.root)+'/$path')
            code=code.replace('"/$(dirname','"'+str(self.root)+'/$(dirname')
            path.write_text(code)
        self.resign_fixture()

    def resign_fixture(self):
        sums=[]
        for p in sorted(self.package.rglob('*')):
            if p.is_file() and p.name!='SHA256SUMS':
                sums.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(self.package).as_posix())
        (self.package/'SHA256SUMS').write_text('\n'.join(sums)+'\n')

    def run_fix(self):
        return subprocess.run(['sh',str(self.package/'install.sh')],env=self.f.env,capture_output=True,text=True)

    def test_apply_preserves_proxy_config_and_only_stops_hotplug_monitor(self):
        old=(self.root/'etc/config/podkop').read_bytes()
        p=self.run_fix();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual((self.root/'etc/config/podkop').read_bytes(),old)
        self.assertEqual(json.loads((self.root/'etc/config/gc').read_text())['config.auto_restart'],'0')
        self.assertEqual((self.root/'calls').read_text().splitlines(),['enabled','stop','disable'])

    def test_modified_program_is_rejected_before_any_service_or_config_change(self):
        p=self.root/'usr/lib/podkop/ufi_startup.sh';p.write_text('local customization')
        r=self.run_fix();self.assertNotEqual(r.returncode,0)
        self.assertEqual((self.root/'etc/config/gc').read_bytes(),self.original)
        self.assertFalse((self.root/'calls').exists())

    def test_failed_gc_commit_restores_previous_setting(self):
        uci=self.f.bin/'uci';uci.write_text(uci.read_text().replace("name=='sing-box'","name=='gc'"))
        self.f.env['FAIL_COMMIT']='1'
        p=self.run_fix();self.assertNotEqual(p.returncode,0)
        self.assertEqual((self.root/'etc/config/gc').read_bytes(),self.original)
        self.assertFalse((self.root/'root/ufi-stability-last-backup').exists())

if __name__=='__main__':unittest.main()
