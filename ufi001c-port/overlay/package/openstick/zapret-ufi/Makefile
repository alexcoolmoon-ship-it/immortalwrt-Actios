# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk
PKG_NAME:=zapret-ufi
PKG_VERSION:=72.13
PKG_RELEASE:=1
PKG_LICENSE:=MIT
STRIP:=:
include $(INCLUDE_DIR)/package.mk
define Package/zapret-ufi
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Official zapret embedded ARM64 build for UFI001C
  DEPENDS:=@aarch64 +kmod-nft-queue +kmod-nfnetlink-queue +nftables-json +curl +ca-bundle +gzip
endef
define Package/zapret-ufi/conffiles
/opt/zapret/config
/opt/zapret/ipset/
endef
define Build/Compile
endef
define Package/zapret-ufi/install
	$(INSTALL_DIR) $(1)/opt/zapret $(1)/etc/init.d $(1)/etc/hotplug.d/iface
	$(CP) ./files/zapret/. $(1)/opt/zapret/
	$(LN) /opt/zapret/init.d/openwrt/zapret $(1)/etc/init.d/zapret
	$(INSTALL_BIN) ./files/zapret/init.d/openwrt/90-zapret $(1)/etc/hotplug.d/iface/90-zapret
endef
$(eval $(call BuildPackage,zapret-ufi))
