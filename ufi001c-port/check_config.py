#!/usr/bin/env python3
"""Fail before compiling if a required UFI001C package was dropped by Kconfig."""
import sys
from pathlib import Path

text = Path(sys.argv[1] if len(sys.argv) > 1 else ".config").read_text()
required = [
    "TARGET_msm89xx_msm8916_DEVICE_openstick-ufi001c",
    "TARGET_ROOTFS_EXT4FS",
    "PACKAGE_kmod-wcn36xx",
    "PACKAGE_kmod-qcom-rproc-wcnss",
    "PACKAGE_kmod-qcom-rproc-modem",
    "PACKAGE_kmod-bam-dmux",
    "PACKAGE_kmod-rpmsg-wwan-ctrl",
    "PACKAGE_ufi001c-radio",
    "PACKAGE_qcom-msm8916-openstick-ufi001c-wcnss-firmware",
    "PACKAGE_qcom-msm8916-wcnss-openstick-ufi001c-nv",
    "PACKAGE_luci-i18n-base-ru",
    "PACKAGE_modemmanager",
    "MODEMMANAGER_WITH_QRTR",
    "LIBQMI_WITH_QRTR_GLIB",
    "PACKAGE_rmtfs",
    "PACKAGE_qrtr-ns",
    "PACKAGE_gc",
    "PACKAGE_ufi001c-defaults",
    "PACKAGE_ufi-care",
    "PACKAGE_podkop",
    "PACKAGE_luci-app-podkop",
    "PACKAGE_luci-i18n-podkop-ru",
    "PACKAGE_podkop-engine",
    "PACKAGE_zapret-ufi",
    "PACKAGE_qmi-utils",
    "PACKAGE_kmod-nft-tproxy",
    "PACKAGE_kmod-nft-socket",
    "PACKAGE_kmod-nft-queue",
    "PACKAGE_kmod-nfnetlink-queue",
    "PACKAGE_kmod-tun",
    "PACKAGE_kmod-inet-diag",
    "PACKAGE_ip-full",
    "PACKAGE_coreutils-od",
]
missing = [name for name in required if f"CONFIG_{name}=y\n" not in text]
if missing:
    raise SystemExit("Required settings missing: " + ", ".join(missing))
if "CONFIG_TARGET_ROOTFS_SQUASHFS=y\n" in text:
    raise SystemExit("This first port requires ext4, not squashfs")
for package in ('sing-box', 'sing-box-tiny', 'podkop-engine-full'):
    if f'CONFIG_PACKAGE_{package}=y\n' in text:
        raise SystemExit('Unexpected second engine provider: ' + package)
print("UFI001C configuration checks passed")
