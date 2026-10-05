#!/usr/bin/env python3
"""Build the helper with the completed firmware toolchain; inspect the ELF."""
import hashlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path

root, out = (Path(x).resolve() for x in sys.argv[1:3])
here = Path(__file__).resolve().parent
cc = list((root / "staging_dir").glob("toolchain-aarch64*/bin/aarch64-openwrt-linux-musl-gcc"))
if len(cc) != 1:
    raise SystemExit(f"Expected one aarch64 toolchain, found {cc}")
out.mkdir(parents=True, exist_ok=True)
binary = out / "enter-fastboot"
subprocess.run([str(cc[0]), "-std=c11", "-Os", "-static", "-s", "-Wall", "-Wextra", "-Werror",
                str(here / "enter-fastboot.c"), "-o", str(binary)], check=True)
data = binary.read_bytes()
assert data[:6] == b"\x7fELF\x02\x01"
assert struct.unpack_from("<HH", data, 16) == (2, 183)
phoff = struct.unpack_from("<Q", data, 32)[0]
phentsize, phnum = struct.unpack_from("<HH", data, 54)
assert all(struct.unpack_from("<I", data, phoff + i * phentsize)[0] != 3 for i in range(phnum))
shutil.copy2(here / "enter-fastboot.c", out / "enter-fastboot.c")
shutil.copy2(here / "README-RU.md", out / "FASTBOOT-RU.md")
with (out / "SHA256SUMS").open("a") as f:
    f.write(f"{hashlib.sha256(data).hexdigest()}  enter-fastboot\n")
print("Static AArch64 helper built and inspected. Hardware execution not tested.")
