# SPDX-License-Identifier: GPL-2.0-only
# Adapted from the HandsomeMod / lkiuyu qcom-firmware package.
include $(TOPDIR)/rules.mk
PKG_NAME:=qcom-firmware
PKG_VERSION:=1034.2.4
PKG_RELEASE:=2
FIRMWARE_REV:=e64f8cfd2a3aabe58633609f33e23c5c9d00730e
STRIP:=:
include $(INCLUDE_DIR)/package.mk

define Download/ufi001c-modem
  FILE:=openstick-ufi001c-modem.bin
  URL:=https://raw.githubusercontent.com/lkiuyu/qcom-firmware/$(FIRMWARE_REV)
  HASH:=c64997f87be2dc3253b8c75ad072d597ffaef564bf515ad7a3bed793b5f22b0a
endef
$(eval $(call Download,ufi001c-modem))

define Download/ufi001c-nv
  FILE:=openstick-ufi001c-nv.bin
  URL:=https://raw.githubusercontent.com/lkiuyu/qcom-firmware/$(FIRMWARE_REV)
  HASH:=f85a3aeadcc9a496493cfd505e11d0110d62ac5d7d7ef7a2a1950e375f2297e1
endef
$(eval $(call Download,ufi001c-nv))

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) $(DL_DIR)/openstick-ufi001c-modem.bin $(PKG_BUILD_DIR)/
	$(CP) $(DL_DIR)/openstick-ufi001c-nv.bin $(PKG_BUILD_DIR)/
endef

define Build/Configure
endef

define Build/Compile
	mkdir -p $(PKG_BUILD_DIR)/openstick-ufi001c
	mcopy -i $(PKG_BUILD_DIR)/openstick-ufi001c-modem.bin -p -m -n -v \
		::image/mba.mbn ::image/modem.* ::image/wcnss.* $(PKG_BUILD_DIR)/openstick-ufi001c
endef

define Package/qcom-firmware-default
  SECTION:=firmware
  CATEGORY:=Firmware
endef

define Package/qcom-msm8916-openstick-ufi001c-wcnss-firmware
  $(Package/qcom-firmware-default)
  TITLE:=Openstick UFI001C WIFI & Bluetooth firmware
endef

define Package/qcom-msm8916-wcnss-openstick-ufi001c-nv
  $(Package/qcom-firmware-default)
  TITLE:=Qualcomm msm8916 WIFI nv data for Handsome Openstick UFI001C
endef

define Package/qcom-msm8916-modem-openstick-ufi001c-firmware
  $(Package/qcom-firmware-default)
  TITLE:=Qualcomm msm8916 modem firmware for Handsome Openstick UFI001C
endef

define Package/qcom-msm8916-openstick-ufi001c-wcnss-firmware/install
	$(INSTALL_DIR) $(1)/lib/firmware
	$(INSTALL_DATA) \
		$(PKG_BUILD_DIR)/openstick-ufi001c/wcnss.* \
		$(1)/lib/firmware
endef

define Package/qcom-msm8916-wcnss-openstick-ufi001c-nv/install
	$(INSTALL_DIR) $(1)/lib/firmware/wlan/prima
	$(INSTALL_DATA) \
		$(PKG_BUILD_DIR)/openstick-ufi001c-nv.bin \
		$(1)/lib/firmware/wlan/prima/WCNSS_qcom_wlan_nv.bin
endef

define Package/qcom-msm8916-modem-openstick-ufi001c-firmware/install
	$(INSTALL_DIR) $(1)/lib/firmware
	$(INSTALL_DATA) \
		$(PKG_BUILD_DIR)/openstick-ufi001c/mba.mbn \
		$(1)/lib/firmware
	$(INSTALL_DATA) \
		$(PKG_BUILD_DIR)/openstick-ufi001c/modem.* \
		$(1)/lib/firmware
endef

$(eval $(call BuildPackage,qcom-msm8916-openstick-ufi001c-wcnss-firmware))
$(eval $(call BuildPackage,qcom-msm8916-wcnss-openstick-ufi001c-nv))
$(eval $(call BuildPackage,qcom-msm8916-modem-openstick-ufi001c-firmware))
