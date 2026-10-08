# Installer 2.1.0 — append-metadata compatibility fix

Historical test record. The user subsequently confirmed a successful v2 boot.
For current v3 instructions and fresh backup paths, read INSTALL-RU.md.

Reported on 2026-10-07: the full backup completed and was verified, but update
stopped at sector alignment, then (after manual boot padding and editing the
checksum file) at `Sparse size/trailer mismatch`. No write command had been
reached in these paths.

Both image recipes use `append-metadata`. Original SHA256SUMS applies to the
complete distributed image, including fwtool records. The installer now:

- verifies source SHA256 before transforming any image;
- checks fwtool framing and cumulative CRC32 using the official format;
- decodes metadata and checks that both images belong to the same build;
- recognizes bounded metadata/signature records, rejects unknown suffixes;
- uses a separate sector-padded boot copy, preserving original images/hashes;
- parses sparse chunks only up to the validated payload boundary;
- completes all image preparation before EDL access;
- preserves GPT, board, backup/NV fingerprint and readback checks;
- exposes `check-images` for an offline preflight.

The fwtool signature envelope CRC is not cryptographic signature authentication.
As before, artifacts must come from the user's trusted build. No signing or
firmware provenance bypass option is added.

`collect.py` now runs the installer's image-preparation path before publishing
an artifact, so the incompatibility is caught by future builds.

Validation: 10 tests passed with `FWTOOL_TEST_BINARY` set to a locally compiled
upstream fwtool from https://lxr.openwrt.org/source/fwtool/ . Tests include valid
upstream-produced records, corrupt/missing/trailing data, boot padding and
source preservation, SHA256 mismatch, preflight before EDL, only rootfs/boot
writes and complete readback in an emulated EDL update. Existing board/layout
and sparse bounds tests still pass. Python compilation checks pass.

Not tested: physical flashing with installer 2.1.0, device boot after this
update, radio/button/Podkop behaviour on the newly written image. Images on
the user's PC were not available here; original files must be restored from
the successful v2 build ZIP before retrying. No new firmware build is needed.

Current user backup: `C:\Users\Alexand\openwrt25-v2\backup-20261007-084228\emmc.bin`.
Its companion `emmc.json` must stay alongside it. Working LTE radio from the
earlier project is unchanged. Source fixes are delivered, not pushed to GitHub.
