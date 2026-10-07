# UFI001C OpenWrt 25.12.5 — v2 source project

Read INSTALL-RU.md for Windows instructions and PROJECT-STATE.md for continuity.
This is source, not compiled or hardware-tested firmware. Upload the archive's
.github and ufi001c-port directories to the existing repository root; start a
new workflow run on the new commit.

Includes own confirmed-working UFI001BC 20211121 radio, Podkop 0.7.23 with
DoH/direct defaults, sing-box 1.12.17, zapret 72.13 ARM64 (filtering disabled
until ISP-specific testing), matching TPROXY/NFQUEUE modules, evdev reset
button handler and AP recovery, and guarded EDL backup/update helper.

Kconfig checks passed; full compilation, boot and physical button tests remain.
Stock GPT/bootloader conversion is not automated. Updater refuses stock layouts.

Build sequence: prepare.py, feeds update/install, finalize_feeds.py, copy
ufi001c.config to .config, make defconfig, check_config.py, make download, make.
Use the provided pinned workflow. Do not force-install foreign kernel modules.

Radio firmware.part-* files are joined and checksum verified during the build.
No per-device NV is included. Podkop GPL-2.0+, zapret MIT; upstream licenses are
retained. Radio binaries come from the user's own stock firmware; this project
is not a new license grant for those binaries.
