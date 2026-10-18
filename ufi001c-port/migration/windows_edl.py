#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Adapter for B. Kerler's unmodified, pinned GPLv3 EDL client.
from pathlib import Path
import os
import runpy
import sys


def main():
    sys.dont_write_bytecode = True
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True, write_through=True)
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    windows = root / 'edlclient/Windows'
    handle = os.add_dll_directory(str(windows)) if os.name == 'nt' else None
    os.environ['PATH'] = str(windows) + os.pathsep + os.environ.get('PATH', '')
    if sys.argv[1:] == ['--self-test']:
        import usb.backend.libusb1
        import usb.core
        import serial
        import docopt
        import Crypto.Cipher.AES
        import Cryptodome.Cipher.AES
        import lxml.etree
        import edlclient.Library.sahara
        import edlclient.Library.firehose_client
        backend = usb.backend.libusb1.get_backend(find_library=lambda name: str(windows / 'libusb-1.0.dll'))
        if backend is None or not list((root / 'Loaders').rglob('007050e1*.bin')):
            raise ValueError('EDL USB backend/loader database missing')
        print('PORTABLE_EDL_SELF_TEST_OK (no USB commands performed)')
        return 0
    sys.argv[0] = str(root / 'edl.py')
    # The upstream code and Firehose command implementation are not patched.
    runpy.run_path(sys.argv[0], run_name='__main__')
    return 0


if __name__ == '__main__':
    sys.exit(main())
