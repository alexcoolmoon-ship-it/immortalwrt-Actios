#!/bin/sh
set -eu
umask 077
die() { echo "ERROR: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die 'Root is required'
[ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = thwc,ufi001c ] || die 'This patch is for UFI001C only'
[ "$(cat /etc/ufi001c-release 2>/dev/null)" = 'UFI001C v3.2.1 Clean / OpenWrt 25.12.5' ] || die 'Expected v3.2.1 Clean firmware'
for tool in uci jq curl dig hexdump base64 sha256sum sing-box; do command -v "$tool" >/dev/null || die "Missing $tool"; done
[ -z "$(uci changes podkop)" ] || die 'Save or discard pending Podkop edits in LuCI first'
[ "$(uci -q get podkop.settings.config_path)" = /etc/sing-box/config.json ] || die 'Unsupported custom config path'
actual=$(sha256sum /usr/bin/podkop | cut -d' ' -f1)
if [ "$actual" = '@PATCH_SHA256@' ]; then
    echo 'PATCH_ALREADY_INSTALLED'
    sh /usr/bin/podkop cloudflare_check && echo 'CLOUDFLARE_DOH_PROXY=PASS' && exit 0
    echo 'CLOUDFLARE_DOH_PROXY=FAIL; run CHECK.cmd and send UFI-CLOUDFLARE.txt'
    exit 3
fi
[ "$actual" = '@BASE_SHA256@' ] || die 'Podkop has other modifications; no files changed'
mkdir /var/lock/ufi-cloudflare.lock 2>/dev/null || die 'Another patch operation is running'
work=$(mktemp -d /tmp/ufi-cloudflare.XXXXXX)
backup=/root/ufi-cloudflare-backup.$(date +%Y%m%d-%H%M%S).$$
changed=0
reload_started=0
cleanup() {
    rc=$?
    trap - EXIT HUP INT TERM
    if [ "$changed" = 1 ]; then
        cp "$backup/podkop.program" /usr/bin/podkop
        chmod 755 /usr/bin/podkop
        cp "$backup/podkop.config" /etc/config/podkop
        chmod 600 /etc/config/podkop
        uci -q revert podkop || true
        cp "$backup/sing-box.json" /etc/sing-box/config.json
        chmod 600 /etc/sing-box/config.json
        [ "$reload_started" != 1 ] || /etc/init.d/podkop reload >/dev/null 2>&1 || true
        echo 'LOCAL_ERROR_ROLLED_BACK'
    fi
    rm -rf "$work"
    rmdir /var/lock/ufi-cloudflare.lock
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
base64 -d > "$work/podkop" <<'UFI_CLOUDFLARE_PROGRAM'
@PROGRAM_BASE64@
UFI_CLOUDFLARE_PROGRAM
[ "$(sha256sum "$work/podkop" | cut -d' ' -f1)" = '@PATCH_SHA256@' ] || die 'Payload checksum mismatch'
sh -n "$work/podkop"
section=$(sh "$work/podkop" cloudflare_section) || die 'Enter and save your proxy URL in Podkop first'
case "$section" in ''|*[!A-Za-z0-9_]*) die 'Invalid proxy section';; esac
mkdir "$backup"
cp /usr/bin/podkop "$backup/podkop.program"
cp /etc/config/podkop "$backup/podkop.config"
cp /etc/sing-box/config.json "$backup/sing-box.json"
chmod 600 "$backup"/*
changed=1
uci set podkop.settings.cloudflare_dns_patch=1
uci set "podkop.settings.cloudflare_dns_section=$section"
uci set podkop.settings.dns_type=doh
uci set podkop.settings.dns_server=1.1.1.1
uci set podkop.settings.bootstrap_dns_server=77.88.8.8
uci set podkop.settings.download_lists_via_proxy=1
uci set "podkop.settings.download_lists_via_proxy_section=$section"
uci commit podkop
sh "$work/podkop" cloudflare_generate "$work/candidate.json" > "$backup/preflight.log" 2>&1 || die 'Generated configuration failed validation; restoring backup'
install_target=/usr/bin/podkop.cloudflare.new
cp "$work/podkop" "$install_target"
chmod 755 "$install_target"
mv "$install_target" /usr/bin/podkop
printf '%s\n' "$backup" > /root/ufi-cloudflare-last-backup.new
mv /root/ufi-cloudflare-last-backup.new /root/ufi-cloudflare-last-backup
reload_started=1
/etc/init.d/podkop reload >/dev/null 2>&1 || die 'Podkop reload failed; restoring backup'
sleep 2
pidof sing-box >/dev/null || die 'Engine did not start; restoring backup'
changed=0
echo 'PATCH_INSTALLED: Cloudflare DoH via proxy; Yandex TCP bootstrap for proxy hostnames'
echo "PROXY_SECTION=$section"
echo "BACKUP=$backup"
echo 'Wi-Fi, LTE bands and FakeIP cache were not changed.'
if sh /usr/bin/podkop cloudflare_check; then
    echo 'CLOUDFLARE_DOH_PROXY=PASS'
else
    echo 'CLOUDFLARE_DOH_PROXY=FAIL; patch remains installed; run CHECK.cmd and send UFI-CLOUDFLARE.txt'
    exit 3
fi
