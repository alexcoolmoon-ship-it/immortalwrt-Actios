#!/usr/bin/env python3
"""Add the USB installer to a new firmware output directory, or package alone."""
from pathlib import Path
import shutil
import sys
from stock_install import PACKAGE_FILES, HERE, check_base

def package(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    if output==HERE:raise ValueError('Choose a separate output directory')
    check_base(HERE/'stock-base')
    for name in PACKAGE_FILES:shutil.copyfile(HERE/name,output/name)
    shutil.copytree(HERE/'stock-base',output/'stock-base',dirs_exist_ok=True)

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Usage: package_stock.py OUTPUT_DIRECTORY')
    package(sys.argv[1])
