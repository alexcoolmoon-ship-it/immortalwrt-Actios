#!/usr/bin/env python3
"""Produce the single USB-install ZIP only after the image/build gates pass."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

PORT = Path(__file__).resolve().parent
sys.path.insert(0, str(PORT / 'migration'))
import stock_install
from check_rootfs import FILES, FORBIDDEN, source_bytes
from runtime_manifest import verify_runtime

VERSION = '3.2.2-clean'
PROFILE = 'UFI001C v3.2.2 Clean / OpenWrt 25.12.5'


def check_reports(images):
    images = Path(images)
    rootfs = json.loads((images / 'routing-rootfs-check.json').read_text())
    if rootfs.get('status') != 'passed' or rootfs.get('profile') != PROFILE:
        raise ValueError('Missing/wrong clean rootfs report')
    if rootfs.get('absent_services') != list(FORBIDDEN):
        raise ValueError('Missing clean-profile service exclusion check')
    for installed, source in FILES.items():
        actual = hashlib.sha256(source_bytes(source)).hexdigest()
        if rootfs.get('files_sha256', {}).get(installed) != actual:
            raise ValueError('Rootfs report is from different source: ' + installed)
    if (images / 'kernel-routing-check.txt').read_text().strip() != 'PASS: built kernel supports IPv4 policy routing for Podkop':
        raise ValueError('Kernel policy routing gate has not passed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('images', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--windows-runtime', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.commit):
        raise ValueError('Expected a full Git commit SHA')
    check_reports(args.images)
    verify_runtime(args.windows_runtime, stock_install.HERE, args.commit)
    info = {'project': 'alexcoolmoon-ship-it/UFI001C', 'version': VERSION,
            'source_commit': args.commit, 'hardware_acceptance': 'pending',
            'image_sha256': {n: stock_install.update.sha(args.images / n) for n in ('boot.img', 'system.img')}}
    (args.images / 'release-info.json').write_text(json.dumps(info, indent=2) + '\n')
    # This reuses the tested image parser, SHA256 check, XHTTP binding and file allowlist.
    stock_install.bundle(argparse.Namespace(firmware=args.images, output=args.output,
                                          windows_runtime=args.windows_runtime))


if __name__ == '__main__':
    main()
