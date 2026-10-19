#!/bin/sh
set -eu
umask 077
[ "$(id -u)" = 0 ] && [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = thwc,ufi001c ] || exit 2
[ -z "$(uci changes podkop)" ] || { echo 'Save/discard pending Podkop edits first'; exit 2; }
backup=$(cat /root/ufi-cloudflare-last-backup)
case "$backup" in /root/ufi-cloudflare-backup.*) ;; *) echo 'Invalid backup'; exit 2;; esac
case "$backup" in *'/../'*|*'/./'*|*[!A-Za-z0-9_./-]*) exit 2;; esac
for name in podkop.program podkop.config sing-box.json; do [ -f "$backup/$name" ] || exit 2; done
mkdir /var/lock/ufi-cloudflare.lock 2>/dev/null || { echo 'Another patch operation is running'; exit 2; }
trap 'rmdir /var/lock/ufi-cloudflare.lock' EXIT
cp "$backup/podkop.program" /usr/bin/podkop
chmod 755 /usr/bin/podkop
cp "$backup/podkop.config" /etc/config/podkop
chmod 600 /etc/config/podkop
uci -q revert podkop || true
cp "$backup/sing-box.json" /etc/sing-box/config.json
chmod 600 /etc/sing-box/config.json
/etc/init.d/podkop reload
echo PATCH_ROLLED_BACK
