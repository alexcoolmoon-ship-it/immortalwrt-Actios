#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Fresh, read-only network verification; writes only its own report directory.
set -u
umask 077
ROOT=/root/ufi-postboot-check
PREVIOUS=/root/ufi-profile/LAST
TARGET='eutran-1|eutran-3|eutran-5|eutran-8|eutran-38|eutran-39|eutran-40|eutran-41'
RUN=''
read_boot() { cat /proc/sys/kernel/random/boot_id; }
read_board() { cat /tmp/sysinfo/board_name 2>/dev/null; }
read_arch() { uname -m; }
mono() { cut -d. -f1 /proc/uptime; }
precise_mono() { cut -d' ' -f1 /proc/uptime; }
cfg() { uci -q get "$1" 2>/dev/null || true; }
health() { /usr/sbin/ufi-lte-health status; }
say() { printf '%s %s\n' "$(mono)" "$*" >>"$RUN/report/progress.txt"; }
valid_token() {
    case "$1" in ''|*[!a-zA-Z0-9_-]*) return 1 ;; esac
    [ "${#1}" -le 80 ]
}
rf_filter() {
    awk 'BEGIN{IGNORECASE=0}
      /rflte_|remoteproc.*(crash|fatal|recover|restart)|Kernel panic|kernel panic|Oops:|watchdog.*(bark|bite|reset|restart)|[Rr]ebooting|[Rr]estarting system|[Rr]eset reason|[Bb]oot reason/ {
        print; n++; if(n>=160) exit
      }'
}
new_kernel_events() {
    dmesg 2>/dev/null | awk -v since="$1" '
      { stamp=$0; sub(/^.*\[/,"",stamp); sub(/\].*$/,"",stamp) }
      stamp ~ /^[ ]*[0-9]+\.[0-9]+$/ && stamp+0>=since &&
      /rflte_|remoteproc.*(crash|fatal|recover|restart)|Kernel panic|kernel panic|Oops:|watchdog.*(bark|bite|reset|restart)|[Rr]ebooting|[Rr]estarting system/ {
        print; n++; if(n>=160) exit
      }'
}
reboot_evidence() {
    local f n=0
    dmesg 2>/dev/null | rf_filter >"$RUN/report/kernel-events.txt"
    : >"$RUN/report/pstore-events.txt"
    for f in /sys/fs/pstore/*; do
        [ -f "$f" ] && [ -r "$f" ] || continue
        n=$((n+1)); [ "$n" -le 8 ] || break
        printf '\nPSTORE FILE: %s\n' "${f##*/}" >>"$RUN/report/pstore-events.txt"
        rf_filter <"$f" >>"$RUN/report/pstore-events.txt"
    done
    [ "$n" -gt 0 ] || printf '%s\n' 'No readable pstore record; previous reboot cause may be unavailable.' >"$RUN/report/pstore-events.txt"
    : >"$RUN/report/watchdog-bootstatus.txt"
    for f in /sys/class/watchdog/watchdog*/bootstatus; do
        [ -r "$f" ] || continue
        printf '%s: ' "$f" >>"$RUN/report/watchdog-bootstatus.txt"
        cat "$f" >>"$RUN/report/watchdog-bootstatus.txt" 2>/dev/null || true
    done
    tr ' ' '\n' </proc/cmdline | awk '/^(androidboot.bootreason|bootreason|reboot_mode|reboot-reason)=/' >"$RUN/report/bootreason-args.txt"
}
previous_run() {
    local previous
    previous=$(cat "$PREVIOUS" 2>/dev/null || true)
    case "$previous" in /root/ufi-profile/run-*) ;; *) return 0 ;; esac
    case "${previous#/root/ufi-profile/run-}" in ''|*[!a-zA-Z0-9_-]*) return 0 ;; esac
    [ -d "$previous" ] && [ ! -L "$previous" ] || return 0
    {
        printf 'Previous result: '; cat "$previous/result" 2>/dev/null || true
        if [ "$(cat "$previous/boot-id" 2>/dev/null)" = "$(read_boot)" ]; then
            printf 'Previous profile run is from the current boot.\n'
        else printf 'Previous profile run boot ID differs from the current boot.\n'; fi
        printf '\nPrevious progress (not a new verification):\n'
        tail -30 "$previous/report/progress.txt" 2>/dev/null || true
    } >"$RUN/report/previous-profile.txt"
}
snapshot() {
    local suffix=$1 key value
    if [ -x /usr/lib/podkop/ufi_dns_health.sh ]; then
        sh /usr/lib/podkop/ufi_dns_health.sh status >"$RUN/report/dns-health-$suffix.txt" 2>&1 || true
    fi
    mmcli --modem=qcom-soc --timeout=12 --output-json >"$RUN/mm-$suffix.private" 2>/dev/null || true
    jq -e '{modem:{generic:(.modem.generic | {
      state, revision, "hardware-revision": .["hardware-revision"],
      "current-modes": .["current-modes"], "current-bands": .["current-bands"],
      "supported-bands": .["supported-bands"]}),
      registration:(.modem["3gpp"] | {"registration-state": .["registration-state"]})}}' \
      "$RUN/mm-$suffix.private" >"$RUN/report/modem-$suffix.json" 2>/dev/null ||
      printf '{"unavailable":true}\n' >"$RUN/report/modem-$suffix.json"
    ubus -t 5 call network.interface.modem status 2>/dev/null |
      jq '{up,pending,available,uptime,proto,device,l3_device}' >"$RUN/report/interface-$suffix.json" 2>/dev/null || true
    ip -4 address show >"$RUN/report/ipv4-$suffix.txt" 2>/dev/null || true
    ip -4 route show table main >"$RUN/report/routes-$suffix.txt" 2>/dev/null || true
    {
        for key in allowedmode preferredmode allowedbands excludedbands auto autoconnect defaultroute; do
            value=$(cfg "network.modem.$key"); printf '%s=%s\n' "$key" "$value"
        done
    } >"$RUN/report/policy-$suffix.txt"
}
profile_matches() {
    jq -e --arg target "$TARGET" '
      .modem.generic.state=="connected" and
      .modem.generic["current-modes"]=="allowed: 4g; preferred: none" and
      (.modem.generic["current-bands"]|sort)==($target|split("|")|sort)' \
      "$RUN/report/modem-end.json" >/dev/null 2>&1 &&
      [ "$(cfg network.modem.allowedmode)" = 4g ] &&
      [ "$(cfg network.modem.allowedbands)" = "$TARGET" ] &&
      [ -z "$(cfg network.modem.excludedbands)" ]
}
proxy_probe() {
    local n=$1 code rc=0
    code=$(curl --noproxy '' --proxy http://127.0.0.1:4534 --connect-timeout 3 --max-time 12 \
      --max-filesize 65536 -fsS -o "$RUN/doh-$n.private" -w '%{http_code}' \
      -H 'accept: application/dns-json' 'https://1.1.1.1/dns-query?name=example.com&type=A' 2>/dev/null) || rc=$?
    case "$code" in [0-9][0-9][0-9]) ;; *) code=unavailable ;; esac
    printf 'sample=%s curl_exit=%s http=%s\n' "$n" "$rc" "$code" >>"$RUN/report/proxy-probes.txt"
    [ "$rc" -eq 0 ] && [ "$code" = 200 ] || return 1
    jq -e 'def ipv4:
      if type!="string" then false else split(".") as $p |
        ($p|length)==4 and all($p[]; length>0 and length<=3 and
          (explode|all(.>=48 and .<=57)) and ((try tonumber catch 999)>=0) and
          ((try tonumber catch 999)<256)) end;
      select(.Status==0) | any(.Answer[]?; .type==1 and (.data|ipv4))' \
      "$RUN/doh-$n.private" >/dev/null 2>&1
}
dns_probe() {
    local n=$1
    dig @127.0.0.1 example.com A +time=3 +tries=1 +short >"$RUN/dns-$n.private" 2>/dev/null || return 1
    awk -F. 'NF==4 && $1~/^[0-9]+$/ && $2~/^[0-9]+$/ && $3~/^[0-9]+$/ && $4~/^[0-9]+$/ &&
      $1<=255 && $2<=255 && $3<=255 && $4<=255 {ok=1} END{exit !ok}' "$RUN/dns-$n.private"
}
collect() {
    local token=$1 start_boot end_boot started_at n health_ok=0 proxy_ok=0 dns_ok=0 result config
    valid_token "$token" || { echo 'Invalid run token'; return 2; }
    [ "$(id -u)" = 0 ] && [ "$(read_arch)" = aarch64 ] && [ "$(read_board)" = thwc,ufi001c ] || {
        echo 'This check is for the existing UFI001C aarch64 installation only.'; return 1; }
    [ ! -L "$ROOT" ] || return 1
    mkdir -p "$ROOT" && chmod 700 "$ROOT" || return 1
    RUN="$ROOT/run-$token"
    mkdir "$RUN" || { echo 'Run already exists. Start CHECK.cmd again for a fresh run.'; return 1; }
    mkdir "$RUN/report" || return 1
    start_boot=$(read_boot)
    started_at=$(precise_mono)
    {
        printf 'Fresh postboot check 1.1\nRun token: %s\nUTC: ' "$token"; date -u
        printf 'Boot ID: %s\nUptime: ' "$start_boot"; cat /proc/uptime
        printf 'Board: '; read_board
        printf 'Architecture: '; read_arch
    } >"$RUN/report/system.txt"
    say 'FRESH_COLLECTION_STARTED: no network, radio or service settings are changed'
    previous_run
    reboot_evidence
    snapshot start
    {
        awk '/^(MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree):/' /proc/meminfo
        df -k / /tmp 2>/dev/null || true
    } >"$RUN/report/resources.txt"
    config=$(cfg podkop.settings.config_path); config=${config:-/etc/sing-box/config.json}
    if command -v sing-box >/dev/null 2>&1; then
        sing-box version 2>/dev/null | head -2 >"$RUN/report/proxy-config-check.txt"
        if sing-box check -c "$config" >/dev/null 2>&1; then
            printf 'Existing sing-box config validation: OK\n' >>"$RUN/report/proxy-config-check.txt"
        else printf 'Existing sing-box config validation: FAILED\n' >>"$RUN/report/proxy-config-check.txt"; fi
    else printf 'sing-box command unavailable\n' >"$RUN/report/proxy-config-check.txt"; fi
    : >"$RUN/report/health-probes.txt"
    : >"$RUN/report/proxy-probes.txt"
    for n in 1 2 3; do
        printf '\nSample %s uptime=%s\n' "$n" "$(mono)" >>"$RUN/report/health-probes.txt"
        if health >>"$RUN/report/health-probes.txt" 2>&1; then health_ok=$((health_ok+1)); fi
        if proxy_probe "$n"; then proxy_ok=$((proxy_ok+1)); say "PROXY_DOH_OK sample=$n"
        else say "PROXY_DOH_FAILED sample=$n"; fi
        if dns_probe "$n"; then dns_ok=$((dns_ok+1)); say "LOCAL_DNS_OK sample=$n"
        else say "LOCAL_DNS_FAILED sample=$n"; fi
        [ "$n" -eq 3 ] || sleep 5
    done
    snapshot end
    reboot_evidence
    new_kernel_events "$started_at" >"$RUN/report/new-kernel-events.txt"
    result=CHECKS_INCOMPLETE
    if [ "$health_ok" -lt 3 ]; then result=LTE_NOT_CONFIRMED
    elif ! jq -e '.modem.generic.state | type=="string"' "$RUN/report/modem-end.json" >/dev/null 2>&1; then result=MODEM_STATUS_UNAVAILABLE
    elif ! profile_matches; then result=LTE_UP_PROFILE_DIFFERS
    elif [ "$proxy_ok" -ne 3 ] || [ "$dns_ok" -ne 3 ]; then result=LTE_UP_DNS_OR_PROXY_UNSTABLE
    else result=CURRENT_BOOT_LTE_DNS_PROXY_OK; fi
    # The short probe interval does not certify long-term RF stability.
    if [ -s "$RUN/report/new-kernel-events.txt" ]; then result=NEW_KERNEL_EVENTS_REVIEW_REQUIRED; fi
    end_boot=$(read_boot)
    [ "$start_boot" = "$end_boot" ] || result=BOOT_CHANGED_DURING_CHECK
    {
        printf 'UFI fresh postboot check 1.1\n%s\n\n' "$result"
        printf 'Run token: %s\nStart boot ID: %s\nEnd boot ID: %s\n' "$token" "$start_boot" "$end_boot"
        printf 'LTE health: %s/3\nProxy DoH: %s/3\nRouter DNS: %s/3\n' "$health_ok" "$proxy_ok" "$dns_ok"
        printf '\nThree fresh probes only; no long-term RF/speed or package-download certification.\n'
        printf 'No LTE band/mode, LAN/Wi-Fi, DNS, proxy config or service state changed.\n'
        printf 'Only whitelisted status data and kernel RF/reboot lines are exported.\n'
        printf 'No APN, IMEI/IMSI/ICCID, proxy URL, UUID, passwords or full config files exported.\n'
    } >"$RUN/report/SUMMARY.txt"
    say "FRESH_COLLECTION_COMPLETED: $result"
    tar -czf "$ROOT/ufi-postboot-report-$token.tar.gz.tmp" -C "$RUN" report &&
      mv "$ROOT/ufi-postboot-report-$token.tar.gz.tmp" "$ROOT/ufi-postboot-report-$token.tar.gz" || return 1
    cat "$RUN/report/SUMMARY.txt"
}
main() {
    case "${1:-}" in
        collect) [ "$#" -eq 2 ] || return 2; collect "$2" ;;
        *) echo 'usage: sh ufi-postboot-check.sh collect RUN_TOKEN'; return 2 ;;
    esac
}
[ "${UFI_POSTBOOT_SOURCE_ONLY:-0}" = 1 ] || main "$@"
