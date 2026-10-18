"""Execute the real button loop with binary input events; no host reset hooks."""
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

PORT = Path(__file__).resolve().parents[1]
SCRIPT = PORT/'overlay/package/openstick/ufi-care/files/usr/sbin/ufi-care'

class ButtonTests(unittest.TestCase):
    def run_events(self, events, clock):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p/'events').write_bytes(b''.join(struct.pack('<qqHHi', 1792140000+i, 123456, *e)
                                            for i,e in enumerate(events)))
            (p/'clock').write_text('\n'.join(map(str,clock))+'\n')
            recover = p/'recover'
            recover.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$RESULT"\n')
            recover.chmod(0o755)
            text = SCRIPT.read_text().split('\nboard_check\ncase ')[0]
            text = text.replace('SELF=/usr/sbin/ufi-care', 'SELF="$TEST_DIR/recover"')
            text += '''
log() { :; }
find_button() { printf '%s\\n' "$TEST_DIR/events"; }
seconds() { head -n1 "$TEST_DIR/clock"; sed -i '1d' "$TEST_DIR/clock"; }
button
'''
            script=p/'button.sh';script.write_text(text)
            env=dict(os.environ,TEST_DIR=str(p),RESULT=str(p/'result'))
            # CI may supply a compiled BusyBox 1.37.0; production is arm64 LE.
            bb=os.environ.get('UFI_TEST_BUSYBOX')
            if bb:
                for name in ('dd','hexdump'):
                    (p/name).symlink_to(Path(bb).resolve())
                env['PATH']=str(p)+os.pathsep+env['PATH']
            command=[bb,'ash',str(script)] if bb else ['sh',str(script)]
            result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertIn('Button stream ended',result.stderr)
            return (p/'result').read_text().splitlines() if (p/'result').exists() else []

    @unittest.skipUnless(shutil.which('hexdump') or os.environ.get('UFI_TEST_BUSYBOX'), 'hexdump not installed')
    def test_real_binary_events_short_medium_long_repeat_and_other_keys(self):
        for held,expected in [(0,[]),(7,[]),(8,['recover network']),
                              (19,['recover network']),(20,['recover settings']),(30,['recover settings'])]:
            with self.subTest(held=held):
                events=[(1,408,1),(1,408,2),(0,0,0),(1,408,0)]
                self.assertEqual(self.run_events(events,[100,100+held]),expected)
        self.assertEqual(self.run_events([(1,408,0),(1,1,1),(1,1,0),(0,0,0)],[]),[])

if __name__=='__main__': unittest.main()
