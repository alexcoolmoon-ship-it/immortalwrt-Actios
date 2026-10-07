#!/usr/bin/env python3
"""Read a saved stock eMMC dump and export its modem partition for inspection.

Standard library only; suitable for the existing Windows edl Python venv.
Opens the input file in rb mode. No USB access and no device writes.
NV contents are not exported; the report records only their lengths and hashes.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import zipfile
import zlib


def read_at(source, offset, length):
    source.seek(offset)
    data = source.read(length)
    if len(data) != length:
        raise ValueError("Backup is truncated at byte %d" % offset)
    return data


def read_gpt(source, disk_size, sector):
    header = read_at(source, sector, sector)
    if header[:8] != b"EFI PART":
        raise ValueError("No GPT signature for sector size %d" % sector)
    header_size, header_crc = struct.unpack_from("<II", header, 12)
    if not 92 <= header_size <= sector:
        raise ValueError("Invalid GPT header length")
    checked = bytearray(header[:header_size])
    checked[16:20] = b"\0" * 4
    if zlib.crc32(checked) & 0xffffffff != header_crc:
        raise ValueError("GPT header checksum mismatch")
    current_lba, backup_lba, first_usable, last_usable = struct.unpack_from("<QQQQ", header, 24)
    table_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", header, 72)
    if current_lba != 1 or disk_size % sector:
        raise ValueError("Not a complete sector-aligned eMMC image")
    if not 1 < first_usable <= last_usable < backup_lba < disk_size // sector:
        raise ValueError("GPT disk boundaries exceed the backup")
    if not (1 <= count <= 4096 and 128 <= entry_size <= 4096 and entry_size % 128 == 0):
        raise ValueError("Invalid GPT partition table dimensions")
    table_size = count * entry_size
    if table_lba < 2 or table_lba * sector + table_size > first_usable * sector:
        raise ValueError("Invalid GPT table location")
    table = read_at(source, table_lba * sector, table_size)
    if zlib.crc32(table) & 0xffffffff != table_crc:
        raise ValueError("GPT partition table checksum mismatch")
    partitions = []
    for i in range(count):
        entry = table[i * entry_size:(i + 1) * entry_size]
        if entry[:16] == b"\0" * 16:
            continue
        start, end = struct.unpack_from("<QQ", entry, 32)
        name = entry[56:128].decode("utf-16-le").split("\0", 1)[0]
        if not first_usable <= start <= end <= last_usable:
            raise ValueError("Invalid partition bounds: %r" % name)
        partitions.append({"name": name, "first_lba": start, "last_lba": end,
                           "offset_bytes": start * sector,
                           "size_bytes": (end - start + 1) * sector})
    ordered = sorted(partitions, key=lambda p: p["first_lba"])
    if any(a["last_lba"] >= b["first_lba"] for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Overlapping GPT partitions")
    return partitions


def copy_and_hash(source, part, target=None):
    source.seek(part["offset_bytes"])
    remaining = part["size_bytes"]
    digest = hashlib.sha256()
    while remaining:
        data = source.read(min(1024 * 1024, remaining))
        if not data:
            raise ValueError("Backup ended inside %s" % part["name"])
        digest.update(data)
        if target is not None:
            target.write(data)
        remaining -= len(data)
    return digest.hexdigest()


def export_radio(dump, output_dir):
    dump = Path(dump).resolve(strict=True)
    output_dir = Path(output_dir).resolve(strict=True)
    if not stat.S_ISREG(dump.stat().st_mode):
        raise ValueError("Input must be a saved regular backup file")
    output = output_dir / "ufi-stock-radio.zip"
    suffix = 2
    while output.exists():
        output = output_dir / ("ufi-stock-radio-%d.zip" % suffix)
        suffix += 1
    with dump.open("rb") as source:
        disk_size = os.fstat(source.fileno()).st_size
        failures = []
        for sector in (512, 4096):
            try:
                partitions = read_gpt(source, disk_size, sector)
                break
            except ValueError as exc:
                failures.append(str(exc))
        else:
            raise ValueError("Cannot validate the stock GPT: " + "; ".join(failures))
        matches = [p for p in partitions if p["name"].lower() == "modem"]
        if len(matches) != 1:
            raise ValueError("Expected one stock partition named 'modem'. Found: " +
                             ", ".join(p["name"] for p in partitions) +
                             ". Use the backup made BEFORE OpenStick/OpenWrt.")
        modem = matches[0]
        if not 512 <= modem["size_bytes"] <= 256 * 1024 * 1024:
            raise ValueError("Unexpected modem partition length")
        report = {"format_version": 1, "backup_name": dump.name,
                  "backup_size_bytes": disk_size, "sector_size": sector,
                  "gpt_primary_header_and_table_crc32": "verified",
                  "partitions": partitions, "nv_hashes": []}
        for part in partitions:
            if part["name"].lower() in ("fsc", "fsg", "modemst1", "modemst2"):
                if part["size_bytes"] > 16 * 1024 * 1024:
                    raise ValueError("Unexpected NV partition length")
                report["nv_hashes"].append({"name": part["name"],
                                            "size_bytes": part["size_bytes"],
                                            "sha256": copy_and_hash(source, part)})
        print("Validated GPT. Reading modem partition: %.1f MiB..." %
              (modem["size_bytes"] / 1048576), flush=True)
        created = False
        try:
            with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as archive:
                created = True
                with archive.open("modem.img", "w", force_zip64=True) as target:
                    report["modem_sha256"] = copy_and_hash(source, modem, target)
                archive.writestr("partition-info.json", json.dumps(report, indent=2))
        except BaseException:
            if created:
                output.unlink(missing_ok=True)
            raise
    print("DONE: " + str(output), flush=True)
    print("Archive size: %.2f MiB. Attach this ZIP in the chat." %
          (output.stat().st_size / 1048576))
    return output


def main():
    if len(sys.argv) != 2:
        print('Usage: python extract-ufi-radio.py "path\\ufi001c_full.bin"')
        return 2
    try:
        export_radio(sys.argv[1], Path(__file__).resolve().parent)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
