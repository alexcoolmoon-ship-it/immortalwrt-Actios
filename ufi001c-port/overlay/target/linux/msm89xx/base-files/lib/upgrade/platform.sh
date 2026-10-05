# SPDX-License-Identifier: GPL-2.0-only
# This port uses separate Android boot and sparse rootfs images, not sysupgrade.
platform_check_image() {
	echo "UFI001C: use the documented fastboot boot/rootfs update procedure."
	return 1
}
