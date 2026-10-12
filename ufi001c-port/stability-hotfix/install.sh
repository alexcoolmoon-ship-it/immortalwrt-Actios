#!/bin/sh
set -eu
umask 077
cd "$(dirname "$0")"
die() { echo "STOP: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die 'Run as root on the modem.'
[ "$(cat /tmp/sysinfo/board_name)" = 'thwc,ufi001c' ] || die 'Expected UFI001C.'
grep -q '^UFI001C v3.1.2 ' /etc/ufi001c-release || die 'This live patch requires UFI001C 3.1.2. Use the new source for a full build.'
sha256sum -c SHA256SUMS >/dev/null || die 'Package checksum mismatch.'
[ -z "$(uci -q changes gc)" ] || die 'Save or discard pending USB settings first.'

while read -r old alternative new mode path; do
    [ ! -L "/$path" ] || die "Unexpected symlink: /$path"
    actual=NEW
    [ ! -e "/$path" ] || actual=$(sha256sum "/$path" | cut -d' ' -f1)
    case "$actual" in "$old"|"$alternative"|"$new") ;; *) die "Unknown/modified file: /$path. No changes made.";; esac
    [ "$(sha256sum "payload/$path" | cut -d' ' -f1)" = "$new" ] || die "Bad payload: $path"
    case "$path" in *.sh|etc/init.d/*|usr/sbin/*) sh -n "payload/$path" || die "Shell syntax: $path";; esac
done < FILES

backup=$(mktemp -d /root/ufi-stability-backup.XXXXXX)
mkdir -p "$backup/files"
cp FILES "$backup/FILES"
cp restore.sh "$backup/restore.sh"
cp -p /etc/config/gc "$backup/gc.config"
/etc/init.d/hotplug-gcreboot enabled >/dev/null 2>&1 && enabled=1 || enabled=0
printf '%s\n' "$enabled" > "$backup/enabled"
while read -r old alternative new mode path; do
    if [ -f "/$path" ]; then
        mkdir -p "$backup/files/$(dirname "$path")"
        cp -p "/$path" "$backup/files/$path"
    fi
done < FILES
echo "BACKUP: $backup"
rollback_on_error() {
    result=$?
    [ "$result" -ne 0 ] || return 0
    trap - EXIT HUP INT TERM
    sh "$backup/restore.sh" || true
    exit "$result"
}
trap rollback_on_error EXIT
trap 'exit 1' HUP INT TERM

# Gate queued events before stopping the extra hotplug monitor. The gadget
# service, network, Wi-Fi and ModemManager are deliberately left running.
while read -r old alternative new mode path; do
    mkdir -p "/$(dirname "$path")"
    cp "payload/$path" "/$path.ufi-new"
    chmod "$mode" "/$path.ufi-new"
    mv "/$path.ufi-new" "/$path"
done < FILES
uci set gc.config.auto_restart=0
uci commit gc
/etc/init.d/hotplug-gcreboot stop
/etc/init.d/hotplug-gcreboot disable
printf '%s\n' "$backup" > /root/ufi-stability-last-backup
trap - EXIT HUP INT TERM
echo 'STABILITY PATCH INSTALLED. Firmware/kernel remain 3.1.2.'
echo 'Reload the LuCI page with Ctrl+F5. Your proxy links and groups were preserved.'
echo 'Run START-CHECK.cmd to restart Podkop and check DNS, then WATCH.cmd.'
