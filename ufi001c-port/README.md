# UFI001C OpenWrt 25.12.5 — v3 XHTTP (3.0-rc1)

Source project and Windows USB installer. This ZIP is not a compiled firmware
image. Read INSTALL-RU.md. PROJECT-STATE.md records completed work and limits.

The v2 image already booted on the user's modem, with working LTE. This revision
replaces ordinary sing-box with **podkop-engine 1.13.21-r12**, using all 86 locked
upstream patches and Go 1.26.8. Podkop 0.7.23 already understands the engine's
XHTTP decoder. Both the feature check and the decoder remain enabled.

The build runs the resulting ARM64 executable under QEMU, checks XHTTP in four
modes, extra/XMUX and DoH, and binds xhttp-validation.json to boot.img/system.img.
An image without this gate cannot be packaged by the new stock installer.

Included: working UFI001BC 20211121 radio; Podkop/DoH; zapret 72.13 (interception
disabled pending carrier-specific strategy); matching TPROXY/NFQUEUE modules;
ufi-care button/AP recovery; existing-layout updater 2.1; guarded stock installer
3.0-rc1 with separate full backups, own NV transfer and readback verification.

First stock conversion is USB/EDL through INSTALL.cmd, not factory web upload.
The factory web update format/signature has not been established. The script
supports only the verified 8 GB factory geometry and matching radio hash.
RESTORE.cmd uses this physical device's full stock backup.

Build with .github/workflows/openwrt25-ufi001c.yml. All base/feed/toolchain/engine
revisions are pinned. The full v3 build and a real stock-unit installation are
still required; offline checks do not establish hardware success.

Radio blobs are from the user's own stock image; no per-device NV is included.
See package licenses, ENGINE-SOURCES.md and migration/BASE-SOURCES.md.
