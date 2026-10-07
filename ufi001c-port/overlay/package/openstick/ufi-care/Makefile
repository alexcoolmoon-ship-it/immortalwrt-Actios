# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk
PKG_NAME:=ufi-care
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
include $(INCLUDE_DIR)/package.mk
define Package/ufi-care
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=UFI001C button and access point recovery
  DEPENDS:=+ubus +uci +jsonfilter +iw
  PKGARCH:=all
endef
define Package/ufi-care/conffiles
/etc/config/ufi-care
/etc/ufi-care/
endef
define Build/Compile
endef
define Package/ufi-care/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/etc/init.d $(1)/etc/config
	$(INSTALL_BIN) ./files/usr/sbin/ufi-care $(1)/usr/sbin/ufi-care
	$(INSTALL_BIN) ./files/etc/init.d/ufi-care $(1)/etc/init.d/ufi-care
	$(INSTALL_CONF) ./files/etc/config/ufi-care $(1)/etc/config/ufi-care
endef
$(eval $(call BuildPackage,ufi-care))
