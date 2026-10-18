#!/usr/bin/env python3
"""Extract the previously verified r12 ARM64 engine for the fast DNS CI gate.

The release build also repeats these live checks with its own exact new engine.
Images are parsed locally; this script performs no USB or host network writes.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

PORT = Path(__file__).resolve().parents[1]
EXPECTED = '799348112c9bd20267a8085f810cc1658b4db9ea6df48965348f1ed2b305ef3a'


def prepare(images, output):
    sys.path.insert(0, str(PORT / 'migration'))
    spec = importlib.util.spec_from_file_location('ufi_reference_update', PORT / 'migration/ufi-install.py')
    update = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(update)
    work = output / 'images'
    work.mkdir(parents=True)
    update.prepare_images(images, work)
    root = output / 'root'
    for directory in ('usr/bin', 'lib'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    for source, destination in (
        ('/usr/bin/sing-box', 'usr/bin/sing-box'),
        ('/lib/libc.so', 'lib/ld-musl-aarch64.so.1'),
        ('/lib/libgcc_s.so.1', 'lib/libgcc_s.so.1'),
    ):
        target = root / destination
        subprocess.run(['debugfs', '-R', 'dump ' + source + ' ' + str(target), str(work / 'rootfs.raw')],
                       check=True, capture_output=True)
        if not target.is_file() or target.stat().st_size < 1000:
            raise ValueError('Missing reference runtime library: ' + source)
    engine = root / 'usr/bin/sing-box'
    if hashlib.sha256(engine.read_bytes()).hexdigest() != EXPECTED:
        raise ValueError('Reference engine does not match the previously checked ARM64 r12 binary')
    engine.chmod(0o755)
    shutil.rmtree(work)
    print('REFERENCE_ARM64_ENGINE_VERIFIED: ' + EXPECTED)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    prepare(args.images.resolve(), args.output.resolve())
