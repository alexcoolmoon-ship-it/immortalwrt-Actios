# SPDX-License-Identifier: GPL-2.0-only

ifeq ($(SUBTARGET),msm8916)

define Device/msm8916
	SOC := msm8916
	CMDLINE := "earlycon console=tty0 console=ttyMSM0,115200 root=/dev/mmcblk0p14 rw rootwait"
endef

define Device/openstick-ufi001c
  $(Device/msm8916)
  DEVICE_VENDOR := Zhihe
  DEVICE_MODEL := OpenStick UFI001C
  DEVICE_DTS := msm8916-thwc-ufi001c
  DEVICE_DTS_DIR := ../dts
  DEVICE_PACKAGES := ufi001c-defaults wpad-basic-mbedtls qcom-msm8916-modem-openstick-ufi001c-firmware qcom-msm8916-openstick-ufi001c-wcnss-firmware qcom-msm8916-wcnss-openstick-ufi001c-nv
endef
TARGET_DEVICES += openstick-ufi001c

endif
