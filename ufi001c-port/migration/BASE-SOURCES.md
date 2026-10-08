# Bundled upstream components

All seven files under stock-base are byte-identical to their counterparts in
OpenStick v1 `base.zip`:
https://github.com/OpenStick/OpenStick/releases/download/v1/base.zip

They were obtained from the pinned mirror used by the earlier installation:
https://github.com/x7780/immortalwrt-Actios/tree/7200f361ae19a001ffff5bb97cb6a89b353f0b35/flashtool/rom

`stock-base/base-manifest.json` records their byte lengths, SHA256 and exact
source URLs. No latest-release URL is fetched while flashing.

The GPT binary is a template, not a ready-to-write disk image: disk-size
placeholders must be filled and CRCs regenerated. The installer implements
this explicitly and checks both resulting GPT copies before writing.

OpenStick's installation replaces the Android partition layout and base
bootchain: https://github.com/OpenStick/OpenStick . Original bootloader source:
https://github.com/OpenStick/lk2nd . A blank devinfo is initialized by its
aboot implementation (`read_device_info_mmc` / `read_device_info`).

Direct EDL physical-sector read/write commands come from:
https://github.com/bkerler/edl/blob/master/edlclient/Library/firehose_client.py

An independent all-EDL migration approach is present at:
https://github.com/hkfuertes/msm8916-openwrt/blob/99d0f10671aa089139e104915608f73c190c4d6c/msm89xx/image/flash.sh
Its board support and geometry differ; its partition table is NOT used here.

The mirror's MIT notice is included as stock-base/LICENSE-MIRROR.txt; it does
not replace any component-specific upstream licensing. Upstream notices and
sources remain at the links above. The Python installer follows the GPL-2.0
license of this project's migration scripts.
