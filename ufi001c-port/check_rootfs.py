#!/usr/bin/env python3
"""Verify that the routing/DNS fixes reached the assembled firmware root."""
import hashlib
import json
from pathlib import Path
import sys

PORT = Path(__file__).resolve().parent
FILES = {
    'etc/ufi001c-release': 'overlay/files/etc/ufi001c-release',
    'usr/bin/podkop': 'overlay/package/openstick/podkop/files/usr/bin/podkop',
    'usr/lib/podkop/ufi_dns_tunnel.sh': 'overlay/package/openstick/podkop/files/usr/lib/ufi_dns_tunnel.sh',
    'usr/lib/podkop/ufi_policy_routing.sh': 'overlay/package/openstick/podkop/files/usr/lib/ufi_policy_routing.sh',
    'etc/uci-defaults/99-ufi001c-addons': 'overlay/package/openstick/ufi001c-defaults/files/99-ufi001c-addons',
}


def verify(root):
    hashes = {}
    for installed, source in FILES.items():
        expected = (PORT / source).read_bytes()
        path = root / installed
        if not path.is_file() or path.read_bytes() != expected:
            raise ValueError('Missing/stale release file in rootfs: ' + installed)
        hashes[installed] = hashlib.sha256(expected).hexdigest()
    for name in ('ip-full', 'od-coreutils'):
        binary = root / 'usr/libexec' / name
        if not binary.is_file():
            raise ValueError('Missing routing/DNS tool: ' + str(binary))
        header = binary.read_bytes()[:20]
        if header[:5] != b'\x7fELF\x02' or header[18:20] != b'\xb7\x00':
            raise ValueError('Expected an AArch64 tool: ' + name)
    return {'status': 'passed', 'profile': (root / 'etc/ufi001c-release').read_text().strip(),
            'files_sha256': hashes, 'tools': ['ip-full', 'od-coreutils'],
            'scope': 'Assembled rootfs contents; no hardware boot test'}


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: check_rootfs.py OPENWRT_BUILD_ROOT')
    roots = list(Path(sys.argv[1]).glob('build_dir/target-*/root-msm89xx'))
    if len(roots) != 1:
        raise SystemExit('Expected exactly one assembled UFI001C rootfs')
    print(json.dumps(verify(roots[0]), indent=2))


if __name__ == '__main__':
    main()
