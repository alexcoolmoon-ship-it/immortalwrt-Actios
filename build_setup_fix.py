#!/usr/bin/env python3
"""Compatibility entry point for the canonical UFI001C setup-fix builder."""
from pathlib import Path
import runpy

CANONICAL = Path(__file__).resolve().parent / 'ufi001c-port/hotfix/build_setup_fix.py'


def build(destination):
    return runpy.run_path(str(CANONICAL))['build'](destination)


if __name__ == '__main__':
    runpy.run_path(str(CANONICAL), run_name='__main__')
