# SPDX-License-Identifier: MIT
include $(TOPDIR)/rules.mk
PKG_NAME:=ufi-service-proxy
PKG_VERSION:=3.1.4
PKG_RELEASE:=1
PKG_LICENSE:=MIT
include $(INCLUDE_DIR)/package.mk

define Package/ufi-service-proxy
  SECTION:=net
  CATEGORY:=Network
  TITLE:=UFI001C package and service downloads through Podkop
  DEPENDS:=+podkop +curl +jq +ca-bundle
  PKGARCH:=all
endef

define Build/Compile
endef

define Package/ufi-service-proxy/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/usr/libexec/ufi-package-bin $(1)/usr/share/ufi-service-proxy
	$(INSTALL_BIN) ./files/apk $(1)/usr/sbin/apk
	$(INSTALL_BIN) ./files/fetch $(1)/usr/libexec/ufi-service-fetch
	$(INSTALL_BIN) ./files/wget $(1)/usr/libexec/ufi-package-bin/wget
	$(INSTALL_BIN) ./files/check $(1)/usr/sbin/ufi-services-check
	$(INSTALL_DATA) ./files/ready.jq $(1)/usr/share/ufi-service-proxy/ready.jq
endef
$(eval $(call BuildPackage,ufi-service-proxy))
