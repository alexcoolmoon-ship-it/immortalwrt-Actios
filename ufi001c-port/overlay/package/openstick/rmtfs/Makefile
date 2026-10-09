#
# Copyright (C) 2020-2021 HandsomeMod Project
#
# This is free software, licensed under the GNU General Public License v2.
# See /LICENSE for more information.
#

include $(TOPDIR)/rules.mk

PKG_NAME:=rmtfs
PKG_VERSION:=1.1.1
PKG_RELEASE:=2

# Pinned upstream commit archive; see OPENSTICK-SOURCES.json for verification.
PKG_SOURCE_VERSION:=586372e575f5cc1a7cbb170219ab6df98f394cae
PKG_SOURCE:=$(PKG_NAME)-$(PKG_SOURCE_VERSION).tar.gz
PKG_SOURCE_URL:=https://codeload.github.com/linux-msm/rmtfs/tar.gz/$(PKG_SOURCE_VERSION)?
PKG_HASH:=7f133f53cb357a1b723e2a954dde252fa4a3db217a489ffa1a654c0b84ab5584
PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_SOURCE_VERSION)


PKG_LICENSE:=BSD-3-Clause

include $(INCLUDE_DIR)/package.mk

define Package/rmtfs
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Qualcomm Remote Filesystem Service Implementation
  DEPENDS:=+libudev-zero +libqrtr +qrtr-ns
endef

define Build/Compile
	$(call Build/Compile/Default,rmtfs)
endef

define Package/rmtfs/install
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./files/rmtfs.init $(1)/etc/init.d/rmtfs

	$(INSTALL_DIR) $(1)/etc/udev/rules.d
	$(INSTALL_BIN) ./files/rmtfs.udev $(1)/etc/udev/rules.d/65-rmtfs.rules

	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/rmtfs $(1)/usr/sbin/
endef

$(eval $(call BuildPackage,rmtfs))
