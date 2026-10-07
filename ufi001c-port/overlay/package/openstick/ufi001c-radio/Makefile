# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk
PKG_NAME:=ufi001c-radio
PKG_VERSION:=20211121
PKG_RELEASE:=1
STRIP:=:
include $(INCLUDE_DIR)/package.mk
define Package/ufi001c-radio
  SECTION:=firmware
  CATEGORY:=Firmware
  TITLE:=UFI001BC 20211121 modem firmware (verified on this UFI001C)
  CONFLICTS:=qcom-msm8916-modem-openstick-ufi001c-firmware
endef
define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)/firmware
	cat ./files/firmware.part-* > $(PKG_BUILD_DIR)/firmware.tar.gz
	cp ./files/archive.sha256 $(PKG_BUILD_DIR)/
	(cd $(PKG_BUILD_DIR) && sha256sum -c archive.sha256)
	tar -xzf $(PKG_BUILD_DIR)/firmware.tar.gz -C $(PKG_BUILD_DIR)/firmware
	(cd $(PKG_BUILD_DIR)/firmware && sha256sum -c SHA256SUMS)
endef
define Build/Compile
endef
define Package/ufi001c-radio/install
	$(INSTALL_DIR) $(1)/lib/firmware
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/firmware/mba.mbn $(PKG_BUILD_DIR)/firmware/modem.* $(1)/lib/firmware/
endef
$(eval $(call BuildPackage,ufi001c-radio))
