#!/usr/bin/env python3
"""Pin the Go host toolchain and select one real sing-box provider."""
import hashlib
import json
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
root = Path(sys.argv[1])
lock = json.loads((here / 'addons.lock.json').read_text())['golang']
relative = Path('packages') / lock['override_path']
source = here / 'feed-overrides' / relative
target = root / 'feeds' / relative
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
if digest(source) != lock['recipe_sha256']:
    raise SystemExit('Go 1.26.8 override checksum mismatch')
if digest(target) not in (lock['original_recipe_sha256'], lock['recipe_sha256']):
    raise SystemExit('Unexpected Go recipe: revalidate pinned packages feed')
target.write_bytes(source.read_bytes())

# Podkop depends on podkop-engine directly. Leaving the stock main/tiny virtual
# providers indexed can create a Kconfig recursion; never disable validation.
recipe = root / 'feeds/packages/net/sing-box/Makefile'
text = recipe.read_text()
if 'PKG_VERSION:=1.12.17' not in text:
    raise SystemExit('Unexpected stock sing-box recipe: revalidate pinned feed')
for package in ('sing-box', 'sing-box-tiny'):
    old = '$(eval $(call BuildPackage,' + package + '))'
    new = '# UFI001C v3: ' + package + ' replaced by podkop-engine with XHTTP'
    if new not in text:
        if text.count(old) != 1:
            raise SystemExit('Unexpected sing-box BuildPackage definitions')
        text = text.replace(old, new)
recipe.write_text(text)
print('Go 1.26.8 and podkop-engine 1.13.21-r12 selected')
