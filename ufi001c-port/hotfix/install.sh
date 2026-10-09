#!/bin/sh
# Apply only the section/setup fixes to the known UFI001C v3.1.1 image.
# No firmware flashing, partition writes, Wi-Fi or modem configuration changes.
set -eu
umask 077
cd "$(dirname "$0")"
die() { echo "STOP: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die 'Run on the modem as root.'
[ "$(cat /tmp/sysinfo/board_name)" = 'thwc,ufi001c' ] || die 'This package is only for thwc,ufi001c.'
grep -Eq 'UFI001C v3\.1\.[12] ' /etc/ufi001c-release || die 'Expected the UFI001C 3.1.1/3.1.2 image.'
sha256sum -c SHA256SUMS >/dev/null || die 'Downloaded package checksum mismatch.'

while read -r old alternative new mode path; do
    [ -f "payload/$path" ] || die "Missing payload: $path"
    actual=$(sha256sum "payload/$path" | cut -d' ' -f1)
    [ "$actual" = "$new" ] || die "Invalid payload: $path"
    [ ! -L "/$path" ] || die "Unexpected symlink: /$path"
    if [ -e "/$path" ]; then
        actual=$(sha256sum "/$path" | cut -d' ' -f1)
        case "$actual" in "$old"|"$alternative"|"$new") ;; *) die "Modified/unknown file: /$path. No changes made." ;; esac
    else
        [ "$old" = NEW ] || die "Installed file is missing: /$path"
    fi
    case "$path" in *.sh|usr/bin/podkop) sh -n "payload/$path" || die "Shell syntax: $path" ;; esac
done < FILES

backup=$(mktemp -d /root/ufi-setup-backup.XXXXXX)
mkdir -p "$backup/files" "$backup/config"
cp FILES "$backup/FILES"
for name in podkop sing-box dhcp; do
    cp -p "/etc/config/$name" "$backup/config/$name"
    uci -q changes "$name" > "$backup/$name.pending" || true
done
while read -r old alternative new mode path; do
    if [ -f "/$path" ]; then
        mkdir -p "$backup/files/$(dirname "$path")"
        cp -p "/$path" "$backup/files/$path"
    fi
done < FILES
/etc/init.d/podkop enabled && was_enabled=1 || was_enabled=0
pidof sing-box >/dev/null 2>&1 && was_running=1 || was_running=0
printf '%s\n' "$was_enabled" > "$backup/enabled"
printf '%s\n' "$was_running" > "$backup/running"
echo "BACKUP: $backup"

rollback_on_error() {
    result=$?
    [ "$result" -ne 0 ] || return 0
    trap - EXIT HUP INT TERM
    echo 'Installation failed; restoring the saved files and settings.' >&2
    while read -r old alternative new mode path; do
        if [ -f "$backup/files/$path" ]; then
            cp -p "$backup/files/$path" "/$path"
        else
            rm -f "/$path"
        fi
        rm -f "/$path.ufi-new"
    done < "$backup/FILES"
    for name in podkop sing-box dhcp; do
        cp -p "$backup/config/$name" "/etc/config/$name"
        uci -q revert "$name" || true
        [ ! -s "$backup/$name.pending" ] || uci batch < "$backup/$name.pending" || true
    done
    /etc/init.d/dnsmasq restart || true
    [ "$was_enabled" = 1 ] && /etc/init.d/podkop enable || /etc/init.d/podkop disable
    [ "$was_running" = 0 ] || /etc/init.d/podkop restart || true
    echo "Backup kept at $backup" >&2
    exit "$result"
}
trap rollback_on_error EXIT
trap 'exit 1' HUP INT TERM

# Save any pending CLI changes before stopping; browser must be closed/reloaded.
uci commit podkop
/etc/init.d/podkop stop
while read -r old alternative new mode path; do
    mkdir -p "/$(dirname "$path")"
    cp "payload/$path" "/$path.ufi-new"
    chmod "$mode" "/$path.ufi-new"
    mv "/$path.ufi-new" "/$path"
done < FILES

uci set 'podkop.settings.ufi_dns_proxy_section=@auto'
uci set 'podkop.settings.download_lists_via_proxy=1'
uci set 'podkop.settings.download_lists_via_proxy_section=@auto'
uci set 'podkop.settings.dns_type=doh'
uci set 'podkop.settings.dns_server=1.1.1.1'
uci set 'podkop.settings.bootstrap_dns_server=77.88.8.8'
uci set 'sing-box.main.dns_fallback=0'
uci set 'sing-box.main.failsafe=0'

# Recreate a form only when all section entries have been deleted.
if ! uci -q show podkop | grep -Eq '=section$'; then
    section=main
    count=1
    while uci -q get "podkop.$section" >/dev/null; do
        section="proxy_$count"
        count=$((count + 1))
    done
    uci set "podkop.$section=section"
    uci set "podkop.$section.connection_type=proxy"
    uci set "podkop.$section.proxy_config_type=url"
    uci set "podkop.$section.proxy_string="
    uci add_list "podkop.$section.community_lists=russia_inside"
fi
uci commit podkop
uci commit sing-box
/etc/init.d/podkop-engine restart
printf '%s\n' 'UFI001C setup fix 3.1.2' > /etc/ufi001c-setup-fix
printf '%s\n' "$backup" > /root/ufi-setup-last-backup
trap - EXIT HUP INT TERM

echo 'SETUP FIX 3.1.2 INSTALLED. The firmware/kernel version remains unchanged.'
if sh /usr/lib/podkop/ufi_startup.sh resolve >/dev/null 2>&1; then
    echo 'A proxy is configured. Run START-CHECK.cmd to start it and verify DNS.'
else
    /etc/init.d/podkop disable
    echo 'Open Podkop -> Sections, paste your own URL, then Save and Apply.'
    echo 'The section may have any name. Then run START-CHECK.cmd.'
fi
echo 'Close the old LuCI tab and reload with Ctrl+F5 before editing.'
