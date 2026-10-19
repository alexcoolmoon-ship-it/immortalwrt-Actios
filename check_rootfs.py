#!/usr/bin/env python3
"""Compatibility entry point for the canonical assembled-rootfs verifier."""
from pathlib import Path
import runpy

CANONICAL = Path(__file__).resolve().parent / 'ufi001c-port/check_rootfs.py'


def verify(root):
    return runpy.run_path(str(CANONICAL))['verify'](Path(root))


if __name__ == '__main__':
    runpy.run_path(str(CANONICAL), run_name='__main__')

