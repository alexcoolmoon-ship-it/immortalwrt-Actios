#!/usr/bin/env python3
"""Verify that the routing/DNS fixes reached the assembled firmware root."""
import hashlib
import json
from pathlib import Path
import sys

PORT = Path(__file__).resolve().parent
FILES = {
    'usr/sbin/ufi-care': 'overlay/package/openstick/ufi-care/files/usr/sbin/ufi-care',
    'lib/netifd/proto/modemmanager.sh': 'overlay/package/openstick/modemmanager/files/lib/netifd/proto/modemmanager.sh',
    'usr/sbin/ufi-lte-health': 'overlay/package/openstick/ufi-lte-health/files/ufi-lte-health',
    'usr/share/ufi-lte-health/session.jq': 'overlay/package/openstick/ufi-lte-health/files/session.jq',
    'etc/init.d/ufi-lte-health': 'overlay/package/openstick/ufi-lte-health/files/init',
    'etc/config/ufi-lte-health': 'overlay/package/openstick/ufi-lte-health/files/config',
    'etc/uci-defaults/99-ufi001c': 'overlay/package/openstick/ufi001c-defaults/files/99-ufi001c',
    'etc/init.d/hotplug-gcreboot': 'overlay/package/openstick/gc/files/hotplug-gcreboot.init',
    'etc/hotplug.d/gcrestart/restart_gc.sh': 'overlay/package/openstick/gc/files/restart_gc.sh',
    'etc/config/gc': 'overlay/package/openstick/gc/files/gc.uci',
    'usr/sbin/ufi-stability-report': 'overlay/files/usr/sbin/ufi-stability-report',
    'etc/ufi001c-release': 'overlay/files/etc/ufi001c-release',
    'usr/bin/podkop': 'overlay/package/openstick/podkop/files/usr/bin/podkop',
    'usr/lib/podkop/ufi_dns_tunnel.sh': 'overlay/package/openstick/podkop/files/usr/lib/ufi_dns_tunnel.sh',
    'usr/lib/podkop/ufi_dns_health.sh': 'overlay/package/openstick/podkop/files/usr/lib/ufi_dns_health.sh',
    'etc/init.d/ufi-dns-health': 'overlay/package/openstick/podkop/files/etc/init.d/ufi-dns-health',
    'etc/init.d/sing-box': 'overlay/package/openstick/podkop-engine/files/sing-box.init',
    'usr/lib/podkop/ufi_policy_routing.sh': 'overlay/package/openstick/podkop/files/usr/lib/ufi_policy_routing.sh',
    'usr/lib/podkop/ufi_startup.sh': 'overlay/package/openstick/podkop/files/usr/lib/ufi_startup.sh',
    'usr/lib/podkop/ufi_fakeip_check.sh': 'overlay/package/openstick/podkop/files/usr/lib/ufi_fakeip_check.sh',
    'www/luci-static/resources/view/podkop/main.js': 'overlay/package/openstick/luci-app-podkop/htdocs/luci-static/resources/view/podkop/main.js',
    'www/luci-static/resources/view/podkop/settings.js': 'overlay/package/openstick/luci-app-podkop/htdocs/luci-static/resources/view/podkop/settings.js',
    'www/luci-static/resources/view/podkop/ufi_sections.js': 'overlay/package/openstick/luci-app-podkop/htdocs/luci-static/resources/view/podkop/ufi_sections.js',
    'etc/uci-defaults/99-ufi001c-addons': 'overlay/package/openstick/ufi001c-defaults/files/99-ufi001c-addons',
    'usr/sbin/apk': 'overlay/package/openstick/ufi-service-proxy/files/apk',
    'usr/libexec/ufi-service-fetch': 'overlay/package/openstick/ufi-service-proxy/files/fetch',
    'usr/libexec/ufi-package-bin/wget': 'overlay/package/openstick/ufi-service-proxy/files/wget',
    'usr/share/ufi-service-proxy/ready.jq': 'overlay/package/openstick/ufi-service-proxy/files/ready.jq',
    'usr/sbin/ufi-services-check': 'overlay/package/openstick/ufi-service-proxy/files/check',
    'usr/libexec/package-manager-call': 'feed-overrides/luci/applications/luci-app-package-manager/root/usr/libexec/package-manager-call',
    'usr/lib/podkop/constants.sh': 'overlay/package/openstick/podkop/files/usr/lib/constants.sh',
    'usr/lib/podkop/helpers.sh': 'overlay/package/openstick/podkop/files/usr/lib/helpers.sh',
    'usr/lib/podkop/sing_box_config_manager.sh': 'overlay/package/openstick/podkop/files/usr/lib/sing_box_config_manager.sh',
    'www/luci-static/resources/view/podkop/podkop.js': 'overlay/package/openstick/luci-app-podkop/htdocs/luci-static/resources/view/podkop/podkop.js',
}


def source_bytes(source):
    data = (PORT / source).read_bytes()
    if source.endswith('/constants.sh'):
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
