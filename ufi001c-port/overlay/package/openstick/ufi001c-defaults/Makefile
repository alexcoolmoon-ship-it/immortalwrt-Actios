# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

PKG_NAME:=ufi001c-defaults
PKG_VERSION:=2
PKG_RELEASE:=1

include $(INCLUDE_DIR)/package.mk

define Package/ufi001c-defaults
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Initial network configuration for OpenStick UFI001C
  DEPENDS:=+gc +luci +luci-proto-modemmanager +modemmanager +resize2fs
  PKGARCH:=all
endef

define Build/Compile
endef

define Package/ufi001c-defaults/install
	$(INSTALL_DIR) $(1)/etc/uci-defaults
	$(INSTALL_BIN) ./files/99-ufi001c* $(1)/etc/uci-defaults/
endef

$(eval $(call BuildPackage,ufi001c-defaults))
