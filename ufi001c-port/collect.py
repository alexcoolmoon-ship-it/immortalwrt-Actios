#!/usr/bin/env python3
"""Collect and structurally validate fastboot images. This is not a boot test."""
import gzip
import hashlib
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
target = root / "bin/targets/msm89xx/msm8916"

def image_file(suffix):
    candidates = sorted(target.glob(f"*openstick-ufi001c*{suffix}"))
    if not candidates:
        candidates = sorted(target.glob(f"*openstick-ufi001c*{suffix}.gz"))
    if len(candidates) != 1:
        raise SystemExit(f"Expected one {suffix}, found: {candidates}")
    data = candidates[0].read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    (out / suffix).write_bytes(data)
    return data

boot = image_file("boot.img")
assert boot[:8] == b"ANDROID!", "Invalid Android boot header"
fields = struct.unpack_from("<10I", boot, 8)
kernel_size, _, ramdisk_size, _, second_size, _, _, page_size, version, _ = fields
assert version == 0 and ramdisk_size == 0 and second_size == 0
assert page_size in (2048, 4096, 8192)
assert page_size + kernel_size <= len(boot) < 64 * 1024 * 1024
cmdline = (boot[64:576].split(b"\0")[0] + boot[608:1632].split(b"\0")[0]).decode()
assert "root=/dev/mmcblk0p14" in cmdline and "rootwait" in cmdline
compressed = boot[page_size:page_size + kernel_size]
decoder = zlib.decompressobj(31)
kernel = decoder.decompress(compressed) + decoder.flush()
assert decoder.eof and kernel[56:60] == b"ARM\x64", "Invalid AArch64 kernel"
dtb = decoder.unused_data
assert dtb[:4] == b"\xd0\x0d\xfe\xed", "Missing appended device tree"
dtb_size = struct.unpack_from(">I", dtb, 4)[0]
assert dtb_size <= len(dtb) and b"thwc,ufi001c\0" in dtb[:dtb_size]

system = image_file("system.img")
magic, major, minor, hdr_size, chdr_size, blk_size, nblocks, nchunks, checksum = struct.unpack_from("<I4H4I", system)
assert magic == 0xed26ff3a and major == 1 and hdr_size >= 28 and chdr_size >= 12
assert blk_size == 4096 and nblocks * blk_size <= 6 * 1024**3
pos = hdr_size
blocks = 0
prefix = bytearray(4096)
for _ in range(nchunks):
    kind, _, count, size = struct.unpack_from("<2H2I", system, pos)
    assert size >= chdr_size and pos + size <= len(system)
    data = system[pos + chdr_size:pos + size]
    if kind == 0xcac1:
        assert len(data) == count * blk_size
        first = data
    elif kind == 0xcac2:
        assert len(data) == 4
        first = data * (min(count * blk_size, 4096) // 4)
    elif kind == 0xcac3:
        assert not data
        first = bytes(min(count * blk_size, 4096))
    elif kind == 0xcac4:
        assert count == 0 and len(data) == 4
        first = b""
    else:
        raise AssertionError(f"Unknown sparse chunk {kind:#x}")
    offset = blocks * blk_size
    if offset < len(prefix):
        take = min(len(first), len(prefix) - offset)
        prefix[offset:offset + take] = first[:take]
    blocks += count
    pos += size
assert blocks == nblocks
assert prefix[1080:1082] == b"\x53\xef", "Root filesystem is not ext4"

for source in (root / "ACCESS.txt", root / ".config", target / "config.buildinfo", target / "version.buildinfo", target / "feeds.buildinfo"):
    if source.is_file():
        shutil.copy2(source, out / ("build.config" if source.name == ".config" else source.name))
report = {
    "target": "UFI001C only; existing OpenStick partition layout",
    "boot_header": "Android v0, gzip AArch64 kernel plus UFI001C DTB",
    "kernel_command_line": cmdline,
    "rootfs": "Android sparse ext4",
    "rootfs_expanded_bytes": nblocks * blk_size,
    "structural_checks": "passed",
    "hardware_boot_test": "not performed",
}
(out / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
(out / "SHA256SUMS").write_text("".join(
    f"{hashlib.sha256((out / name).read_bytes()).hexdigest()}  {name}\n"
    for name in ("boot.img", "system.img")
))
print(json.dumps(report, indent=2))

# Keep selected modules from THIS build. Other targets' modules are not ABI-compatible.
apk_out = out / "same-build-apks"
apk_out.mkdir(exist_ok=True)
prefixes = ("kmod-nft-tproxy-", "kmod-nft-socket-", "kmod-nft-queue-",
            "kmod-nfnetlink-queue-", "kmod-tun-", "kmod-inet-diag-",
            "ufi-care-", "podkop-", "luci-app-podkop-", "zapret-ufi-")
for apk in (root / "bin").rglob("*.apk"):
    if apk.name.startswith(prefixes):
        shutil.copy2(apk, apk_out / apk.name)
