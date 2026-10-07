#!/usr/bin/env python3
"""Run after feeds install: one real sing-box provider avoids a Kconfig cycle."""
import sys
from pathlib import Path
p=Path(sys.argv[1])/'feeds/packages/net/sing-box/Makefile'
s=p.read_text()
old='$(eval $(call BuildPackage,sing-box-tiny))'
new='# UFI001C: full sing-box only; tiny virtual provider creates a recursive dependency with podkop'
if new not in s:
    if s.count(old)!=1 or 'PKG_VERSION:=1.12.17' not in s:
        raise SystemExit('Unexpected sing-box recipe: revalidate pinned feed')
    p.write_text(s.replace(old,new))
print('Pinned full sing-box provider ready')
