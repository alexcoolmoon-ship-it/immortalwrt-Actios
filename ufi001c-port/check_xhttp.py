#!/usr/bin/env python3
"""Execute the actual ARM64 engine under QEMU; no network connections are made."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlencode


def verify(binary, command, output):
    binary = Path(binary).resolve()
    elf = binary.read_bytes()
    if elf[:5] != b'\x7fELF\x02' or elf[18:20] != b'\xb7\x00':
        raise ValueError('The checked firmware engine must be AArch64 ELF64')
    lock = json.loads((Path(__file__).with_name('addons.lock.json')).read_text())['podkop-engine']

    def run(*args):
        result = subprocess.run([*map(str, command), *args], text=True,
                                capture_output=True, timeout=90)
        if result.returncode:
            raise ValueError('Engine check failed: ' + result.stderr[-4000:])
        return result.stdout

    version = run('version')
    if 'sing-box version ' + lock['expected_version'] + '\n' not in version:
        raise ValueError('Unexpected engine version: ' + version)
    features = set()
    for line in version.splitlines():
        if line.startswith('Features: '):
            features.update(line[10:].split(','))
    if not set(lock['required_features']) <= features:
        raise ValueError('Missing XHTTP / decode-link features')

    probes = []
    with tempfile.TemporaryDirectory(prefix='ufi-xhttp-') as work:
        config_file = Path(work) / 'config.json'
        for mode in ('auto', 'packet-up', 'stream-up', 'stream-one'):
            queries = {'type': 'xhttp', 'security': 'tls', 'sni': 'example.com',
                       'host': 'example.com', 'path': '/ufi-check', 'mode': mode,
                       'fp': 'chrome'}
            link = ('vless://11111111-1111-4111-8111-111111111111@127.0.0.1:443?'
                    + urlencode(queries))
            outbound = json.loads(run('tools', 'decode-link', '--compact', link))
            if outbound.get('transport', {}).get('type') != 'xhttp':
                raise ValueError('decode-link did not preserve XHTTP')
            if outbound['transport'].get('mode', 'auto') != mode:
                raise ValueError('decode-link did not preserve XHTTP mode')
            outbound['tag'] = 'probe'
            config = {'outbounds': [outbound], 'route': {'final': 'probe'}}
            config_file.write_text(json.dumps(config))
            run('check', '-c', str(config_file))
            probes.append('VLESS + TLS + XHTTP ' + mode)

        # These extra fields are easily lost when a wrapper URL-decodes twice.
        queries['mode'] = 'packet-up'
        queries['extra'] = json.dumps({
            'uplinkHTTPMethod': 'POST', 'uplinkDataPlacement': 'header',
            'uplinkDataKey': 'X-Data', 'sessionIDPlacement': 'header',
            'sessionIDKey': 'X-Session', 'seqPlacement': 'header',
            'seqKey': 'X-Seq', 'xmux': {'maxConnections': 2}}, separators=(',', ':'))
        link = ('vless://11111111-1111-4111-8111-111111111111@127.0.0.1:443?'
                + urlencode(queries))
        outbound = json.loads(run('tools', 'decode-link', '--compact', link))
        transport = outbound['transport']
        if (transport.get('uplink_data_placement') != 'header'
                or transport.get('uplink_data_key') != 'X-Data'
                or str(transport.get('xmux', {}).get('max_connections')) != '2'):
            raise ValueError('XHTTP extra/XMUX options lost in decode-link')
        outbound['tag'] = 'probe'
        config_file.write_text(json.dumps({'outbounds': [outbound], 'route': {'final': 'probe'}}))
        run('check', '-c', str(config_file))
        probes.append('XHTTP extra: header uplink + XMUX')

        # DoH support must remain present in the slim build.
        config_file.write_text(json.dumps({
            'dns': {'servers': [{'type': 'https', 'tag': 'doh', 'server': '1.1.1.1',
                                 'path': '/dns-query'}], 'final': 'doh'},
            'outbounds': [{'type': 'direct', 'tag': 'direct'}]}))
        run('check', '-c', str(config_file))
        probes.append('DNS over HTTPS')

    report = {'status': 'passed', 'architecture': 'aarch64',
              'engine_version': lock['expected_version'],
              'engine_sha256': hashlib.sha256(elf).hexdigest(),
              'features': sorted(features), 'checks': probes,
              'scope': 'QEMU ARM64 decode-link and config validation; no live proxy/LTE test'}
    Path(output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_root', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    binaries = list(args.build_root.glob('build_dir/target-*/root-msm89xx/usr/bin/sing-box'))
    if len(binaries) != 1:
        raise SystemExit('Expected exactly one sing-box in the final firmware root: ' + str(binaries))
    binary = binaries[0].resolve()
    sysroot = binary.parents[2]
    qemu = shutil.which('qemu-aarch64') or shutil.which('qemu-aarch64-static')
    if not qemu:
        raise SystemExit('Install qemu-user for the mandatory ARM64 engine gate')
    report = verify(binary, [qemu, '-L', str(sysroot), str(binary)], args.report)
    images = [args.report.parent / name for name in ('boot.img', 'system.img')]
    if not all(image.is_file() for image in images):
        raise SystemExit('Run collect.py before the XHTTP gate to bind the result to both images')
    report['image_sha256'] = {image.name: hashlib.sha256(image.read_bytes()).hexdigest()
                              for image in images}
    args.report.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
