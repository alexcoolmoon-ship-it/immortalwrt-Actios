#!/usr/bin/env python3
"""Check the working radio bundle before starting the lengthy firmware build."""
import hashlib
import io
import tarfile
from pathlib import Path


def verify():
    here = Path(__file__).resolve().parent
    folder = here / 'overlay/package/openstick/ufi001c-radio/files'
    parts = sorted(folder.glob('firmware.part-*'))
    if [p.name for p in parts] != ['firmware.part-01', 'firmware.part-02']:
        raise ValueError('Expected both original radio archive parts')
    archive = b''.join(p.read_bytes() for p in parts)
    expected = (folder / 'archive.sha256').read_text().split()[0]
    if hashlib.sha256(archive).hexdigest() != expected:
        raise ValueError('Radio source archive SHA256 mismatch; restore original parts')
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        lines = source.extractfile('SHA256SUMS').read().decode('ascii').splitlines()
        for line in lines:
            digest, name = line.split(None, 1)
            name = name.strip().lstrip('*')
            member = source.getmember(name)
            if not member.isfile() or member.size > 128 * 1024**2:
                raise ValueError('Invalid radio file: ' + name)
            if hashlib.sha256(source.extractfile(member).read()).hexdigest() != digest:
                raise ValueError('Radio file SHA256 mismatch: ' + name)
    print(f'Working UFI001BC 20211121 radio verified: {len(lines)} files')


if __name__ == '__main__':
    verify()
