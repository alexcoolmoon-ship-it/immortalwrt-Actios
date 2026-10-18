#!/usr/bin/env python3
"""Select the upstream Podkop profile, without UFI background recovery services."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
CLEAN = HERE / 'clean'
REMOVED_PACKAGES = ('ufi-care', 'ufi-lte-health', 'ufi-service-proxy', 'zapret-ufi')


def verify_source():
    manifest = json.loads((CLEAN / 'podkop-upstream.json').read_text())
    base = CLEAN / 'package/openstick'
    actual = {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in base.rglob('*') if p.is_file()}
    expected = dict(manifest['files_sha256'])
    expected.update(manifest['modified_sha256'])
    if actual != expected:
        raise ValueError('Clean Podkop source differs from its recorded upstream/DNS patch manifest')


def apply(root):
    root = Path(root)
    verify_source()
    packages = root / 'package/openstick'
    for name in (*REMOVED_PACKAGES, 'podkop', 'luci-app-podkop'):
        shutil.rmtree(packages / name, ignore_errors=True)
    shutil.copytree(CLEAN / 'package/openstick', packages, dirs_exist_ok=True)
    shutil.copy2(CLEAN / 'ufi001c.config', root / 'ufi001c.config')
    shutil.copy2(CLEAN / '99-ufi001c-addons', packages / 'ufi001c-defaults/files/99-ufi001c-addons')
    shutil.copy2(CLEAN / 'ufi001c-release', root / 'files/etc/ufi001c-release')
    (root / 'files/usr/sbin/ufi-stability-report').unlink(missing_ok=True)
    # Keep the XHTTP engine and its list-recovery hook, without a second daemon
    # that stops Podkop or rewrites DNS after a crash loop.
    recipe = packages / 'podkop-engine/Makefile'
    text = recipe.read_text()
    for name in ('podkop-engine.init', 'podkop-engine.uci-defaults', 'failsafe'):
        lines = [line for line in text.splitlines(True)
                 if not ('$(INSTALL_BIN)' in line and './files/' + name + ' ' in line)]
        if len(lines) != len(text.splitlines(True)) - 1:
            raise ValueError('Unexpected engine install recipe: ' + name)
        text = ''.join(lines)
    recipe.write_text(text)


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
