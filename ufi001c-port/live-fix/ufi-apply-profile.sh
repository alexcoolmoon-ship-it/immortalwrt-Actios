#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Apply the observed per-radio policy without flashing or modifying LAN/Wi-Fi.
# No timeout/nohup applet or jq regex support is required.
set -u
umask 077
ROOT=/root/ufi-profile
LOCK=/tmp/ufi-profile.lock
SELF=$(readlink -f "$0")
TARGET='eutran-1|eutran-3|eutran-5|eutran-8|eutran-38|eutran-39|eutran-40|eutran-41'
SAFE='eutran-1|eutran-3'
RUN=''
ARMED=0
cfg() { uci -q get "$1" 2>/dev/null || true; }
mono() { cut -d. -f1 /proc/uptime; }
boot() { cat /proc/sys/kernel/random/boot_id; }
mm_json() { mmcli --modem=qcom-soc --timeout=12 --output-json 2>/dev/null; }
say() { printf '%s %s\n' "$(mono)" "$*" | tee -a "$RUN/report/progress.txt"; }
result() { printf '%s\n' "$1" >"$RUN/result"; }
mask_matches() {
    mm_json | jq -e --arg bands "$1" '
      .modem.generic.state=="connected" and
      (.modem.generic["current-bands"]|sort)==($bands|split("|")|sort) and
      .modem.generic["current-modes"]=="allowed: 4g; preferred: none"' >/dev/null 2>&1
}
preflight() {
    local cmd data hash changes old
    [ "$(id -u)" = 0 ] && [ "$(uname -m)" = aarch64 ] &&
      [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = thwc,ufi001c ] || {
        echo 'Only the identified aarch64 UFI001C is supported'; return 1; }
    for cmd in mmcli uci ubus jq ip dmesg sha256sum ifdown ifup curl dig awk tar; do
        command -v "$cmd" >/dev/null 2>&1 || { echo "Missing command: $cmd"; return 1; }
    done
    for cmd in /tmp/ufi-diagnose.lock /tmp/ufi-finish.lock /tmp/ufi-proxy-recover.lock; do
        [ ! -d "$cmd" ] || { echo 'A diagnostic/recovery lock exists; no changes made'; return 1; }
    done
    [ "$(cfg network.modem.proto)" = modemmanager ] &&
      [ "$(cfg network.modem.device)" = qcom-soc ] &&
      [ "$(cfg network.modem.allowedmode)" = 4g ] || { echo 'Unexpected modem configuration'; return 1; }
    for cmd in auto autoconnect defaultroute; do
        [ "$(cfg network.modem.$cmd)" != 0 ] || { echo "Modem $cmd disabled"; return 1; }
    done
    changes=$(uci -q changes network) || { echo 'Cannot inspect pending network changes'; return 1; }
    [ -z "$changes" ] || { echo 'Save or revert pending NETWORK changes in LuCI first'; return 1; }
    # These installed protocols apply an explicit band mask BEFORE --enable.
    hash=$(sha256sum /lib/netifd/proto/modemmanager.sh 2>/dev/null | cut -d' ' -f1)
    case "$hash" in
        fcb4f10049158ac687dd621ac0a6fd0f431e8ddebfd4aad2a6a86222737cfb3e|94fce0583f5e7bec3fcdeb4b167aeaf45e7e47fd5b63f82c18bc504a06b86615) ;;
        *) echo 'Unknown netifd protocol; do not apply a mask after radio enable'; return 1 ;;
    esac
    [ -x /usr/sbin/ufi-lte-health ] && /usr/sbin/ufi-lte-health status >/dev/null 2>&1 || {
        echo 'Stable LTE kernel link/address/route required before this change'; return 1; }
    old=$(cfg network.modem.allowedbands)
    case "$old" in
        'eutran-1|eutran-3|eutran-7'|"$TARGET"|"$SAFE")
            [ -z "$(cfg network.modem.excludedbands)" ] || return 1 ;;
        supported-lte)
            [ "$(cfg network.modem.excludedbands)" = 'eutran-7|eutran-20' ] || return 1 ;;
        *) echo 'Custom band policy: no automatic overwrite'; return 1 ;;
    esac
    data=$(mm_json) || { echo 'ModemManager unavailable'; return 1; }
    printf '%s' "$data" | jq -e '
      .modem.generic as $g |
      $g.state=="connected" and $g["hardware-revision"]=="10000" and
      ($g.revision | split(" ")|map(select(length>0))|.[0:2])==["UFI001BC","20211121"] and
      ($g["supported-bands"]|map(select(startswith("eutran-")))|sort)==
        (["eutran-1","eutran-3","eutran-5","eutran-7","eutran-8","eutran-20",
          "eutran-38","eutran-39","eutran-40","eutran-41"]|sort)' >/dev/null || {
        echo 'Different radio/revision/band list; this profile is not approved for it'; return 1; }
    dmesg | awk '/\[[ ]*[0-9]+\.[0-9]+\]/ {ok=1} END {exit !ok}' || {
        echo 'Cannot monitor timestamped RF events'; return 1; }
}
rf_failed() {
    dmesg | awk -v since="$(cat "$RUN/since")" '
      {s=$0; sub(/^.*\[/,"",s); sub(/\].*$/,"",s); if (s+0>=since) print}' |
      grep -E 'rflte_|remoteproc remoteproc0: (crash detected|handling crash)' >"$RUN/rf-current.private"
    [ -s "$RUN/rf-current.private" ] || return 1
    cat "$RUN/rf-current.private" >>"$RUN/report/new-rf-events.txt"
    return 0
}
set_policy() {
    uci set network.modem.allowedmode=4g &&
      uci set "network.modem.allowedbands=$1" || return 1
    uci -q delete network.modem.excludedbands || true
    uci -q delete network.modem.preferredmode || true
    uci commit network
}
connect_policy() {
    local n up
    say "Applying LTE-only mask: $1"
    set_policy "$1" || return 1
    ifdown modem >>"$RUN/actions.private" 2>&1 || return 1
    # Wait for netifd teardown before disabling MM. Only this interface is touched.
    n=0
    while [ "$n" -lt 15 ]; do
        up=$(ubus -t 4 call network.interface.modem status 2>/dev/null |
          jq -r '(.up==false) and (.pending!=true)' 2>/dev/null)
        [ "$up" = true ] && break
        n=$((n+1)); sleep 1
    done
    [ "$up" = true ] || return 1
    mmcli --modem=qcom-soc --timeout=20 --disable >>"$RUN/actions.private" 2>&1 || return 1
    ifup modem >>"$RUN/actions.private" 2>&1
}
wait_lte() {
    local since n=0
    since=$(mono)
    while [ $(( $(mono)-since )) -lt 180 ]; do
        if rf_failed; then say 'New RF failure observed; no automatic exclusion guessing'; return 1; fi
        if /usr/sbin/ufi-lte-health status >/dev/null 2>&1 && mask_matches "$1"; then
            n=$((n+1)); [ "$n" -lt 3 ] || { say 'LTE_READY: mask, mode, link/address/default route verified'; return 0; }
        else n=0; fi
        sleep 5
    done
    say 'LTE did not settle within the time limit'; return 1
}
observe_lte() {
    local since lost=0
    since=$(mono)
    while [ $(( $(mono)-since )) -lt 120 ]; do
        rf_failed && { say 'RF failed during the observation window'; return 1; }
        if /usr/sbin/ufi-lte-health status >/dev/null 2>&1 && mask_matches "$TARGET"; then lost=0
        else lost=$((lost+1)); [ "$lost" -lt 3 ] || { say 'LTE session repeatedly lost'; return 1; }; fi
        sleep 5
    done
    say 'LTE_OBSERVED: two minutes without new RF failure; speed/other cells not certified'
}
proxy_once() {
    local code
    code=$(curl --noproxy '' --proxy http://127.0.0.1:4534 --connect-timeout 3 --max-time 12 -fsS \
      -o "$RUN/doh.private" -w '%{http_code}' -H 'accept: application/dns-json' \
      'https://1.1.1.1/dns-query?name=example.com&type=A' 2>/dev/null) || {
        case "$code" in [0-9][0-9][0-9]) say "PROXY_DOH_HTTP_$code" ;; *) say 'PROXY_DOH_TRANSPORT_FAILED' ;; esac
        return 1; }
    jq -e 'def ipv4:
      if type!="string" then false else split(".") as $p |
        ($p|length)==4 and all($p[]; length>0 and length<=3 and
          (explode|all(.>=48 and .<=57)) and ((try tonumber catch 999)>-1) and
          ((try tonumber catch 999)<256)) end;
      select(.Status==0) | any(.Answer[]?; .type==1 and (.data|ipv4))' "$RUN/doh.private" >/dev/null 2>&1
}
dns_once() {
    dig @127.0.0.1 example.com A +time=3 +tries=1 +short 2>/dev/null |
      awk -F. 'NF==4 && $1~/^[0-9]+$/ && $2~/^[0-9]+$/ && $3~/^[0-9]+$/ && $4~/^[0-9]+$/ &&
        $1<=255 && $2<=255 && $3<=255 && $4<=255 {ok=1} END {exit !ok}'
}
verify_proxy() {
    local config n=0 good=0 bad=0 restarted=0
    config=$(cfg podkop.settings.config_path); config=${config:-/etc/sing-box/config.json}
    jq -e -f /usr/share/ufi-service-proxy/ready.jq "$config" >/dev/null 2>&1 &&
      sing-box check -c "$config" >/dev/null 2>&1 || {
        say 'PROXY_NOT_CONFIGURED: LTE profile kept, no template started'; return 1; }
    while [ "$n" -lt 9 ]; do
        n=$((n+1))
        /usr/sbin/ufi-lte-health status >/dev/null 2>&1 || { say 'LTE lost during proxy check'; return 1; }
        rf_failed && { say 'New RF failure during proxy verification'; return 1; }
        if proxy_once && dns_once; then
            bad=0; good=$((good+1)); say "DNS_PROXY_SAMPLE_OK $good/3"
            if [ "$good" -ge 3 ]; then
                /usr/sbin/ufi-services-check >"$RUN/report/package-fetch.txt" 2>&1 || {
                    say 'PACKAGE_FETCH_FAILED: LTE mask kept'; return 1; }
                say 'DNS_PROXY_PACKAGES_OK'; return 0
            fi
        else good=0; bad=$((bad+1)); say 'DNS_PROXY_SAMPLE_FAILED (no direct-DNS fallback)'; fi
        if [ "$bad" -ge 3 ] && [ "$restarted" -eq 0 ]; then
            # One bounded restart of the existing validated engine, never radio/Wi-Fi.
            say 'Restarting configured sing-box once after stable LTE'
            /etc/init.d/sing-box restart >>"$RUN/actions.private" 2>&1 || return 1
            restarted=1
        fi
        sleep 5
    done
    say 'DNS_PROXY_UNSTABLE: LTE mask kept; inspect upstream XHTTP/server separately'
    return 1
}
fallback() {
    # Never restore B7 from the old B1/B3/B7 backup automatically.
    say 'FALLBACK: keeping LTE-only B1/B3, without the two reproduced RF triggers'
    printf '%s\n' "$SAFE" >"$RUN/report/applied-mask.txt"
    cut -d' ' -f1 /proc/uptime >"$RUN/since"
    connect_policy "$SAFE" && wait_lte "$SAFE" || {
        result FALLBACK_SAVED_CONNECTION_NOT_CONFIRMED; return 1; }
    result FALLBACK_B1_B3
    verify_proxy || true
}
valid_run() {
    case "$1" in "$ROOT"/run-*) ;; *) return 1 ;; esac
    [ -d "$1/report" ] && [ ! -L "$1" ]
}
package() {
    { printf 'UFI LTE profile fix 1.0\n'; cat "$RUN/result";
      printf '\nExcluded on this tested radio/unit: B7, B20. LTE only.\nApplied mask: ';
      cat "$RUN/report/applied-mask.txt" 2>/dev/null || true;
      printf '\n'; tail -18 "$RUN/report/progress.txt" 2>/dev/null || true;
    } >"$RUN/report/SUMMARY.txt"
    tar -czf "$ROOT/ufi-profile-report.tar.gz.tmp.$$" -C "$RUN" report &&
      mv "$ROOT/ufi-profile-report.tar.gz.tmp.$$" "$ROOT/ufi-profile-report.tar.gz"
}
finish() {
    trap - EXIT
    trap '' HUP INT TERM
    if [ "$ARMED" = 1 ]; then fallback || true; fi
    if [ "$(cat "$RUN/result" 2>/dev/null)" = IN_PROGRESS ]; then result INTERRUPTED; fi
    package || true
    touch "$RUN/DONE"
    if [ "$(cat "$LOCK/run" 2>/dev/null)" = "$RUN" ]; then rm -f "$LOCK/run"; rmdir "$LOCK" 2>/dev/null || true; fi
}
worker() {
    trap finish EXIT
    trap '' HUP
    trap 'exit 130' INT TERM
    preflight || { result PREFLIGHT_FAILED; return; }
    cp -p /etc/config/network "$RUN/network.backup.private" || { result BACKUP_FAILED; return; }
    ARMED=1
    cut -d' ' -f1 /proc/uptime >"$RUN/since"
    printf '%s\n' "$TARGET" >"$RUN/report/applied-mask.txt"
    if ! connect_policy "$TARGET" || ! wait_lte "$TARGET" || ! observe_lte; then
        ARMED=0; fallback || true; return
    fi
    ARMED=0
    if verify_proxy; then result PROFILE_APPLIED_CHECKS_OK
    else result PROFILE_APPLIED_PROXY_UNSTABLE; fi
    if rf_failed; then fallback || true; fi
}
start() {
    local name
    preflight || return 1
    [ ! -L "$ROOT" ] && [ ! -L "$LOCK" ] || return 1
    mkdir -p "$ROOT" && chmod 700 "$ROOT" || return 1
    mkdir "$LOCK" 2>/dev/null || { echo 'Already running or old lock; use CONTINUE'; return 1; }
    name="$(date -u +%Y%m%d-%H%M%S)-$$"; RUN="$ROOT/run-$name"
    mkdir -p "$RUN/report" || { rmdir "$LOCK"; return 1; }
    boot >"$RUN/boot-id"; printf '%s\n' "$RUN" >"$ROOT/LAST"; printf '%s\n' "$RUN" >"$LOCK/run"
    result IN_PROGRESS
    (trap '' HUP; exec sh "$SELF" _worker "$RUN") >"$RUN/worker.private" 2>&1 </dev/null &
    echo 'Profile change started in background; an SSH interruption does not restart it.'
}
same_boot_or_stop() {
    [ "$(boot)" != "$(cat "$RUN/boot-id")" ] || return 0
    result REBOOT_INTERRUPTED_VERIFY_LTE; package
    cat "$RUN/report/SUMMARY.txt"
    echo 'The device rebooted. Old worker cannot resume. Run CHECK-LTE.cmd from the new USB bundle for fresh verification.'
    return 3
}
wait_run() {
    local since old='' line
    since=$(mono)
    same_boot_or_stop || return 3
    while [ ! -f "$RUN/DONE" ]; do
        same_boot_or_stop || return 3
        line=$(tail -1 "$RUN/report/progress.txt" 2>/dev/null || true)
        if [ "$line" != "$old" ]; then echo "$line"; old=$line; fi
        [ $(( $(mono)-since )) -lt 900 ] || { echo 'Wait limit reached; use CONTINUE later'; return 1; }
        sleep 5
    done
    same_boot_or_stop || return 3
    cat "$RUN/report/SUMMARY.txt"
}
main() {
    case "${1:-status}" in
        new-wait) start && wait_run ;;
        _worker)
            RUN=${2:-}; valid_run "$RUN" && [ "$(boot)" = "$(cat "$RUN/boot-id")" ] || return 1
            worker ;;
        wait|export|status)
            RUN=$(cat "$ROOT/LAST" 2>/dev/null || true); valid_run "$RUN" || return 1
            case "$1" in
                wait) wait_run ;;
                export) package ;;
                status) cat "$RUN/result"; tail -5 "$RUN/report/progress.txt" 2>/dev/null || true ;;
            esac ;;
        *) echo 'usage: sh ufi-apply-profile.sh new-wait|wait|export|status'; return 2 ;;
    esac
}
[ "${UFI_PROFILE_SOURCE_ONLY:-0}" = 1 ] || main "$@"
