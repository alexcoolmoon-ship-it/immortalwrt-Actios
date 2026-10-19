#!/usr/bin/env python3
"""Build the offline, reversible DNS-only patch for the exact clean firmware."""
import argparse
import base64
import hashlib
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
PORT = HERE.parent
BASE = HERE / 'podkop-v3.2.1'


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Unexpected base program: patch anchor is missing/duplicated')
    return text.replace(old, new)


def patched_program():
    text = BASE.read_text()
    text = once(text, '    sing_box_additional_inbounds\n    sing_box_save_config',
                '    sing_box_additional_inbounds\n    sing_box_cloudflare_patch || return 1\n    sing_box_save_config')
    text = once(text, '    case "$dns_type" in\n    doh|dot)',
                '    local cloudflare_enabled\n    config_get_bool cloudflare_enabled settings cloudflare_dns_patch 0\n'
                '    [ "$cloudflare_enabled" != 1 ] || dns_type=tcp\n    case "$dns_type" in\n    doh|dot)')
    text = once(text, '    if [ "$dns_type" = "doh" ]; then\n        # Check',
                '    local cloudflare_enabled\n    config_get_bool cloudflare_enabled settings cloudflare_dns_patch 0\n'
                '    if [ "$cloudflare_enabled" = 1 ]; then\n'
                '        display_dns_server="1.1.1.1 (proxy)"\n'
                '        if cloudflare_doh_query_ok; then dns_status=1; fi\n'
                '    elif [ "$dns_type" = "doh" ]; then\n        # Check')
    pos = text.rindex('\ncase "$1" in')
    text = text[:pos] + '\n' + (HERE / 'functions.sh').read_text() + text[pos:]
    pos = text.rindex('\ncase "$1" in') + len('\ncase "$1" in')
    text = text[:pos] + '\ncloudflare_section) cloudflare_proxy_section ;;\ncloudflare_check) cloudflare_doh_query_ok ;;\ncloudflare_generate) cloudflare_generate "$2" ;;' + text[pos:]
    return text


def build(output):
    program = patched_program().encode()
    installer = (HERE / 'install.template.sh').read_text()
    installer = installer.replace('@BASE_SHA256@', hashlib.sha256(BASE.read_bytes()).hexdigest())
    installer = installer.replace('@PATCH_SHA256@', hashlib.sha256(program).hexdigest())
    encoded = base64.b64encode(program).decode()
    installer = installer.replace('@PROGRAM_BASE64@', '\n'.join(encoded[n:n+76] for n in range(0, len(encoded), 76)))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        prefix = 'UFI001C-Cloudflare-Patch/'
        archive.writestr(prefix + 'install.sh', installer)
        for name in ('APPLY.cmd', 'CHECK.cmd', 'ROLLBACK.cmd', 'rollback.sh', 'check.sh', 'README-RU.txt'):
            data = (HERE / name).read_bytes()
            if name.endswith('.cmd'):
                data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            archive.writestr(prefix + name, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('Corrupt generated patch archive')
    print(output)
    print('SHA256:', hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    build(parser.parse_args().output)
