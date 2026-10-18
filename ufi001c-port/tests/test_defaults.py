"""Run first-boot defaults with isolated UCI, board, key and firewall helpers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PORT=Path(__file__).resolve().parents[1]
SOURCE=PORT/'overlay/package/openstick/ufi001c-defaults/files/99-ufi001c'


class Defaults(unittest.TestCase):
    def run_defaults(self,existing):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); (p/'bin').mkdir()
            (p/'board').write_text('thwc,ufi001c\n')
            (p/'key').write_text('TEST-WIFI-KEY\n')
            (p/'functions').write_text('config_load() { :; }\nconfig_foreach() { :; }\n')
            (p/'bin/uci').write_text('''#!/bin/sh
if [ "$*" = '-q get network.modem' ]; then
    if [ "$EXISTING" = 1 ]; then echo interface; exit 0; else exit 1; fi
fi
printf '%s\\n' "$*" >>"$CASE/calls"
if [ "$*" = '-q batch' ]; then cat >>"$CASE/batch"; fi
''')
            (p/'bin/uci').chmod(0o755)
            s=SOURCE.read_text()
            for old,new in {'/tmp/sysinfo/board_name':p/'board',
                            '/etc/ufi001c-wifi-key':p/'key',
                            '/lib/functions.sh':p/'functions',
                            '/etc/apk/repositories.d/distfeeds.list':p/'nonexistent-feed'}.items():
                s=s.replace(old,str(new))
            script=p/'defaults.sh'; script.write_text(s)
            env=dict(os.environ,CASE=tmp,EXISTING=str(int(existing)),PATH=str(p/'bin')+os.pathsep+os.environ['PATH'])
            run=subprocess.run(['sh',str(script)],env=env,capture_output=True,text=True,timeout=5)
            return run, (p/'calls').read_text() if (p/'calls').exists() else '', (p/'batch').read_text() if (p/'batch').exists() else ''

    def test_saved_modem_configuration_is_not_reinitialized(self):
        p,calls,batch=self.run_defaults(True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(calls,''); self.assertEqual(batch,'')

    def test_clean_first_boot_uses_the_postboot_verified_explicit_lte_mask(self):
        p,calls,batch=self.run_defaults(False)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn("set network.modem.allowedmode='4g'",batch)
        self.assertIn("set network.modem.allowedbands='eutran-1|eutran-3|eutran-5|eutran-8|eutran-38|eutran-39|eutran-40|eutran-41'",batch)
        self.assertNotIn('set network.modem.excludedbands=',batch)
        self.assertIn("set wireless.default_radio0.key='TEST-WIFI-KEY'",batch)


if __name__=='__main__': unittest.main()
