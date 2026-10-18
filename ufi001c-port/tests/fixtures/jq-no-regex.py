#!/usr/bin/python3
"""Fault injection: run real jq with regex functions unavailable, as on device.

This is not another jq build. JSON parsing and every non-regex operation use
the real jq executable. Calling a regex function fails, reproducing the report.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

DISABLED = '''
def test($r): error("jq was compiled without ONIGURUMA regex library");
def test($r;$f): error("jq was compiled without ONIGURUMA regex library");
def match($r): error("jq was compiled without ONIGURUMA regex library");
def match($r;$f): error("jq was compiled without ONIGURUMA regex library");
def capture($r): error("jq was compiled without ONIGURUMA regex library");
def capture($r;$f): error("jq was compiled without ONIGURUMA regex library");
def sub($r;s): error("jq was compiled without ONIGURUMA regex library");
def sub($r;s;$f): error("jq was compiled without ONIGURUMA regex library");
def gsub($r;s): error("jq was compiled without ONIGURUMA regex library");
def gsub($r;s;$f): error("jq was compiled without ONIGURUMA regex library");
def scan($r): error("jq was compiled without ONIGURUMA regex library");
def scan($r;$f): error("jq was compiled without ONIGURUMA regex library");
def splits($r): error("jq was compiled without ONIGURUMA regex library");
def splits($r;$f): error("jq was compiled without ONIGURUMA regex library");
'''

def main():
    args = sys.argv[1:]
    real = os.environ.get('UFI_TEST_REAL_JQ', '/usr/bin/jq')
    if any(a in args for a in ('--version', '-V', '--help', '-h', '--build-configuration')):
        os.execv(real, [real, *args])
    i = 0
    temporary = None
    while i < len(args):
        arg = args[i]
        if arg in ('--arg', '--argjson', '--slurpfile', '--rawfile', '--argfile'):
            i += 3
        elif arg in ('-L', '--indent', '--sort-keys'):
            i += 1 if arg == '--sort-keys' else 2
        elif arg in ('-f', '--from-file'):
            with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as out:
                out.write(DISABLED + Path(args[i + 1]).read_text())
                temporary = out.name
            args[i + 1] = temporary
            break
        elif arg.startswith('-'):
            i += 1
        else:
            args[i] = DISABLED + arg
            break
    try:
        return subprocess.call([real, *args])
    finally:
        if temporary:
            Path(temporary).unlink()

if __name__ == '__main__':
    sys.exit(main())
