# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk
PKG_NAME:=ufi-lte-health
PKG_VERSION:=1.0.0
PKG_RELEASE:=3
PKG_LICENSE:=GPL-2.0-only
include $(INCLUDE_DIR)/package.mk
define Package/ufi-lte-health
  SECTION:=net
  CATEGORY:=Network
  TITLE:=UFI001C bounded LTE link recovery
  DEPENDS:=+modemmanager +ubus +uci +jq +ip-full
  PKGARCH:=all
endef
define Package/ufi-lte-health/conffiles
/etc/config/ufi-lte-health
endef
define Build/Compile
endef
define Package/ufi-lte-health/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/usr/share/ufi-lte-health $(1)/etc/init.d $(1)/etc/config
	$(INSTALL_BIN) ./files/ufi-lte-health $(1)/usr/sbin/ufi-lte-health
	$(INSTALL_DATA) ./files/session.jq $(1)/usr/share/ufi-lte-health/session.jq
	$(INSTALL_BIN) ./files/init $(1)/etc/init.d/ufi-lte-health
	$(INSTALL_CONF) ./files/config $(1)/etc/config/ufi-lte-health
endef
$(eval $(call BuildPackage,ufi-lte-health))
