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
from check_rootfs import FILES

VERSION = '3.1.2'


def check_reports(images):
    images = Path(images)
    rootfs = json.loads((images / 'routing-rootfs-check.json').read_text())
    if rootfs.get('status') != 'passed' or rootfs.get('profile') != 'UFI001C v3.1.2 Setup / OpenWrt 25.12.5':
        raise ValueError('Missing/wrong v3.1.2 rootfs report')
    for installed, source in FILES.items():
        actual = hashlib.sha256((PORT / source).read_bytes()).hexdigest()
        if rootfs.get('files_sha256', {}).get(installed) != actual:
            raise ValueError('Rootfs report is from different source: ' + installed)
    if (images / 'kernel-routing-check.txt').read_text().strip() != 'PASS: built kernel supports IPv4 policy routing for Podkop':
        raise ValueError('Kernel policy routing gate has not passed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('images', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.commit):
        raise ValueError('Expected a full Git commit SHA')
    check_reports(args.images)
    info = {'project': 'alexcoolmoon-ship-it/UFI001C', 'version': VERSION,
            'source_commit': args.commit, 'hardware_acceptance': 'pending',
            'image_sha256': {n: stock_install.update.sha(args.images / n) for n in ('boot.img', 'system.img')}}
    (args.images / 'release-info.json').write_text(json.dumps(info, indent=2) + '\n')
    # This reuses the tested image parser, SHA256 check, XHTTP binding and file allowlist.
    stock_install.bundle(argparse.Namespace(firmware=args.images, output=args.output))


if __name__ == '__main__':
    main()
