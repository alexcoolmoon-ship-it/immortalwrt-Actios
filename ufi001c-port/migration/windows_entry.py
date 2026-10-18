#!/usr/bin/env python3
"""Frozen installer entry point. EDL is a separate GPLv3 executable."""
from pathlib import Path
import sys


def main():
    sys.dont_write_bytecode = True
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True, write_through=True)
    import stock_install
    import portable_runtime
    if getattr(sys, 'frozen', False):
        stock_install.HERE = Path(sys.executable).resolve().parent.parent
    if sys.argv[1:] == ['--self-test']:
        portable_runtime.self_test()
        return 0
    if sys.argv[1:] == ['driver-help']:
        return portable_runtime.driver_help()
    if len(sys.argv) == 1:
        sys.argv.append('install')
    return stock_install.main()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        sys.exit(1)
