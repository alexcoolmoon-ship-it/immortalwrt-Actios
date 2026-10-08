# v3 XHTTP / installer 3.0-rc1 validation — 2026-10-07

Passed locally:
- OpenWrt 25.12.5 f0a60eee + pinned packages/luci feeds, real make defconfig
  followed by check_config.py. Mandatory radio/driver/ModemManager/Podkop/
  podkop-engine/NFQUEUE/TPROXY packages remain selected. No Kconfig recursion.
- All 86 upstream podkop-engine 1.13.21-r12 patches applied to the locked
  sing-box source with patch --fuzz=0.
- Upstream ARM64 main-variant binary, from the hash-verified release package,
  executed using QEMU 8.2.2. Version/features, XHTTP auto/packet-up/stream-up/
  stream-one, URL-encoded extra with header uplink/XMUX, and DoH config checks
  passed. No live Internet/proxy handshake in these tests.
- Working radio archive SHA256 and all 23 contained file hashes.
- 21 offline migration tests, including real fwtool metadata, source image
  preservation, sparse expansion, both GPT CRCs, stock geometry/radio rejection,
  own NV transfer, current-device binding, per-write readback, stop on bad
  readback, full-restore orchestration, safe ZIP handling, XHTTP report binding.

The final firmware binary is compiled on the user's GitHub Actions runner.
The workflow repeats the ARM64 engine gate using THAT binary in THAT rootfs,
then records SHA256 for both distributed images in xhttp-validation.json.
The stock installer requires this report and engine config before device writes.
A locally tested upstream binary is not substituted into the firmware.

Not performed: a full v3 kernel/rootfs build in this workspace, actual Windows
USB execution against the next stock modem, physical stock conversion and
rollback, physical reset button/AP recovery checks, LTE/proxy/DoH operation on
v3 hardware. Thus the distribution remains an RC1, not a hardware-qualified
final release. The user's prior successful boot and LTE apply to v2.

See migration/VALIDATION-STOCK.md and ENGINE-SOURCES.md for exact scope and
upstream package modifications. No factory NV or private proxy credentials
are shipped. The original metadata/geometry/checksum guards remain in place.
