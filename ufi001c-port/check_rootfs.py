#!/usr/bin/env python3
"""Verify that the routing/DNS fixes reached the assembled firmware root."""
import hashlib
import json
from pathlib import Path
import sys

PORT = Path(__file__).resolve().parent
FILES = {
    'lib/netifd/proto/modemmanager.sh': 'overlay/package/openstick/modemmanager/files/lib/netifd/proto/modemmanager.sh',
    'etc/uci-defaults/99-ufi001c': 'overlay/package/openstick/ufi001c-defaults/files/99-ufi001c',
    'etc/init.d/hotplug-gcreboot': 'overlay/package/openstick/gc/files/hotplug-gcreboot.init',
    'etc/hotplug.d/gcrestart/restart_gc.sh': 'overlay/package/openstick/gc/files/restart_gc.sh',
    'etc/config/gc': 'overlay/package/openstick/gc/files/gc.uci',
    'etc/ufi001c-release': 'clean/ufi001c-release',
    'etc/init.d/sing-box': 'overlay/package/openstick/podkop-engine/files/sing-box.init',
    'etc/uci-defaults/99-ufi001c-addons': 'clean/99-ufi001c-addons',
}
# Check every installed upstream Podkop library/UI/ACL, including the version
# substitutions made by the OpenWrt recipes. Legacy overlays cannot pass here.
base = PORT / 'clean/package/openstick'
for package, source_subdir, destination in (
    ('podkop', 'files/etc', 'etc'),
    ('podkop', 'files/usr/bin', 'usr/bin'),
    ('podkop', 'files/usr/lib', 'usr/lib/podkop'),
    ('luci-app-podkop', 'root', ''),
    ('luci-app-podkop', 'htdocs', 'www'),
):
    folder = base / package / source_subdir
    for path in folder.rglob('*'):
        if path.is_file():
            installed = str(Path(destination) / path.relative_to(folder))
            FILES[installed] = str(path.relative_to(PORT))

FORBIDDEN = (
    'usr/sbin/ufi-care', 'etc/init.d/ufi-care',
    'usr/sbin/ufi-lte-health', 'etc/init.d/ufi-lte-health',
    'etc/init.d/ufi-dns-health', 'usr/lib/podkop/ufi_dns_health.sh',
    'usr/lib/podkop/ufi_dns_tunnel.sh', 'usr/lib/podkop/ufi_startup.sh',
    'usr/sbin/apk', 'usr/libexec/ufi-service-fetch',
    'usr/libexec/ufi-package-bin', 'usr/sbin/ufi-services-check',
    'usr/sbin/ufi-stability-report', 'etc/init.d/zapret',
    'etc/init.d/podkop-engine', 'usr/libexec/podkop-engine/failsafe',
)



def source_bytes(source):
    data = (PORT / source).read_bytes()
    if source.endswith(('/constants.sh', '/main.js')):
        version = json.loads((PORT / 'addons.lock.json').read_text())['podkop']['tag']
        data = data.replace(b'__COMPILED_VERSION_VARIABLE__', version.encode())
    return data


def verify(root):
    hashes = {}
    for installed, source in FILES.items():
        expected = source_bytes(source)
        path = root / installed
        if not path.is_file() or path.read_bytes() != expected:
            raise ValueError('Missing/stale release file in rootfs: ' + installed)
        hashes[installed] = hashlib.sha256(expected).hexdigest()
    for installed in FORBIDDEN:
        if (root / installed).exists() or (root / installed).is_symlink():
            raise ValueError('Unwanted service/wrapper in clean rootfs: ' + installed)
    for name in ('ip-full',):
        binary = root / 'usr/libexec' / name
        if not binary.is_file():
            raise ValueError('Missing routing/DNS tool: ' + str(binary))
        header = binary.read_bytes()[:20]
        if header[:5] != b'\x7fELF\x02' or header[18:20] != b'\xb7\x00':
            raise ValueError('Expected an AArch64 tool: ' + name)
    return {'status': 'passed', 'profile': (root / 'etc/ufi001c-release').read_text().strip(),
            'files_sha256': hashes, 'tools': ['ip-full'], 'absent_services': list(FORBIDDEN),
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
