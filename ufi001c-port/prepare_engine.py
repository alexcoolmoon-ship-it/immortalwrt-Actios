#!/usr/bin/env python3
"""Unpack the locked upstream XHTTP patches into the build tree, before feeds."""
import hashlib
import json
import tarfile
from pathlib import Path


def prepare(root):
    here = Path(__file__).resolve().parent
    lock = json.loads((here / 'addons.lock.json').read_text())['podkop-engine']
    archive = here / 'podkop-engine-patches.tar.gz'
    manifest = json.loads((here / 'podkop-engine-patches.json').read_text())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock['patches_archive_sha256']:
        raise ValueError('podkop-engine patch archive checksum mismatch')
    if len(manifest) != lock['patches_count']:
        raise ValueError('podkop-engine patch manifest count mismatch')
    verified = {}
    with tarfile.open(archive) as source:
        for member in source:
            if (not member.isfile() or '/' in member.name or '\\' in member.name
                    or member.name not in manifest or member.name in verified
                    or member.size > 4 * 1024 * 1024):
                raise ValueError('Unexpected podkop-engine archive entry')
            data = source.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != manifest[member.name]:
                raise ValueError('podkop-engine patch checksum mismatch: ' + member.name)
            verified[member.name] = data
    if set(verified) != set(manifest):
        raise ValueError('Missing podkop-engine patches')
    destination = Path(root) / 'package/openstick/podkop-engine/patches'
    destination.mkdir(parents=True, exist_ok=True)
    if set(p.name for p in destination.iterdir()) - set(verified):
        raise ValueError('Unrecognised extra engine patches in build directory')
    for name, data in verified.items():
        (destination / name).write_bytes(data)
    print(f'Prepared podkop-engine {lock["version"]}: {len(verified)} verified patches')


if __name__ == '__main__':
    import sys
    prepare(sys.argv[1])
