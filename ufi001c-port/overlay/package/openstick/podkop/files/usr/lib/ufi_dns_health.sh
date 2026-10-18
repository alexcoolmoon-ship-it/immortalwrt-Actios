#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Bounded recovery for a live process whose DNS/proxy path stopped responding.
# No reboot, radio reset, routing flush, UCI writes or unencrypted DNS fallback.
set -eu
umask 077
state=/var/run/ufi-dns-health.state
lock=/var/run/ufi-dns-health.lock
snapshot=/var/run/ufi-dns-health.last-failure
fingerprint=/var/run/ufi-dns-health.config
helper=/usr/lib/podkop/ufi_dns_tunnel.sh
cfg() { uci -q get "$1" 2>/dev/null || true; }

eligible() {
    [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = thwc,ufi001c ] &&
    [ -f /var/run/ufi-podkop.active ] &&
    [ "$(cfg podkop.settings.ufi_dns_health)" != 0 ] &&
    [ "$(cfg podkop.settings.shutdown_correctly)" = 0 ] &&
    [ -n "$(cfg podkop.settings.ufi_dns_proxy_section)" ] &&
    [ "$(cfg sing-box.main.enabled)" = 1 ] || return 1
    /usr/sbin/ufi-lte-health status >/dev/null 2>&1 || return 1
    conf=$(cfg podkop.settings.config_path)
    conf=${conf:-/etc/sing-box/config.json}
    [ -s "$conf" ]
}

take_lock() {
    if ! mkdir "$lock" 2>/dev/null; then
        oldpid=$(cat "$lock/pid" 2>/dev/null || true)
        case "$oldpid" in ''|*[!0-9]*) return 1;; esac
        kill -0 "$oldpid" 2>/dev/null && return 1
        rm -f "$lock/pid"
        rmdir "$lock" 2>/dev/null || return 1
        mkdir "$lock" 2>/dev/null || return 1
    fi
    echo $$ >"$lock/pid"
    trap 'rm -f "$lock/pid"; rmdir "$lock" 2>/dev/null || true' EXIT
    trap 'exit 1' INT TERM HUP
}

load_state() {
    now=$(cut -d. -f1 /proc/uptime)
    start=$now; attempts=0; last=0; failed=0
    [ -e "$state" ] || return 0
    values=$(jq -er --argjson now "$now" '
      select(type == "array" and length == 4) |
      select(all(.[]; type == "number" and . >= 0 and . <= $now and . == floor)) |
      select(.[0] <= $now and .[1] <= 3 and .[2] <= $now and .[3] <= 3) | join(" ")' "$state") || return 1
    read -r start attempts last failed <<EOF
$values
EOF
    [ "$last" -eq 0 ] || [ "$last" -ge "$start" ] || return 1
    if [ $((now - start)) -ge 3600 ]; then start=$now; attempts=0; last=0; fi
}

save_state() {
    printf '[%s,%s,%s,%s]\n' "$start" "$attempts" "$last" "$failed" >"$state.tmp.$$"
    mv "$state.tmp.$$" "$state"
}

record_failure() {
    {
        echo "uptime=$now external_doh=$external local_engine_dns=$engine local_router_dns=$router action=$action"
        awk '/^(MemTotal|MemAvailable|SwapFree):/' /proc/meminfo
        printf 'loadavg='; cat /proc/loadavg
        for name in nf_conntrack_count nf_conntrack_max; do
            [ ! -r "/proc/sys/net/netfilter/$name" ] || { printf '%s=' "$name"; cat "/proc/sys/net/netfilter/$name"; }
        done
        for pid in $(pidof sing-box 2>/dev/null || true); do
            case "$pid" in ''|*[!0-9]*) continue;; esac
            [ ! -r "/proc/$pid/status" ] || awk '/^(Name|VmRSS|Threads):/' "/proc/$pid/status"
            [ ! -r "/proc/$pid/limits" ] || grep 'Max open files' "/proc/$pid/limits" || true
            [ ! -d "/proc/$pid/fd" ] || { printf 'open_fds='; find "/proc/$pid/fd" -mindepth 1 -maxdepth 1 | wc -l; }
        done
    } >"$snapshot.tmp.$$"
    mv "$snapshot.tmp.$$" "$snapshot"
}

tick() {
    take_lock || return 0
    load_state || { logger -t ufi-dns-health 'Invalid recovery state; skipping automatic restart'; return 1; }
    if ! eligible; then failed=0; save_state; return 0; fi
    identity=$(sha256sum "$conf")
    if [ "$(cat "$fingerprint" 2>/dev/null || true)" != "$identity" ]; then failed=0; fi
    printf '%s\n' "$identity" >"$fingerprint.tmp.$$"
    mv "$fingerprint.tmp.$$" "$fingerprint"
    external=0; engine=0; router=0
    sh "$helper" probe-dns >/dev/null 2>&1 && external=1
    # A unique name prevents cached positive answers from concealing failures.
    query="ufi-health-$now-$$.example.com"
    sh "$helper" probe-local 127.0.0.42 "$query" >/dev/null 2>&1 && engine=1
    sh "$helper" probe-local 127.0.0.1 "$query" >/dev/null 2>&1 && router=1
    if [ "$external$engine$router" = 111 ]; then failed=0; save_state; return 0; fi
    [ "$failed" -ge 3 ] || failed=$((failed + 1))
    save_state
    [ "$failed" -ge 3 ] || return 0
    [ "$attempts" -lt 3 ] && { [ "$last" -eq 0 ] || [ $((now - last)) -ge 300 ]; } || return 0
    # Recheck intentional stops, LTE reattachments and config changes after probes.
    if ! eligible || [ "$(sha256sum "$conf")" != "$identity" ]; then failed=0; save_state; return 0; fi
    sing-box check -c "$conf" >/dev/null 2>&1 || return 1
    action=sing-box
    [ "$external$engine" != 11 ] || action=dnsmasq
    attempts=$((attempts + 1)); last=$now; failed=0
    save_state # Count failed restart attempts too.
    record_failure
    logger -t ufi-dns-health "Three failed DNS checks; restarting $action (attempt $attempts/3 per hour, 300s cooldown); state: $snapshot"
    "/etc/init.d/$action" restart
}

case "${1:-status}" in
    tick) tick;;
    watch)
        sleep 90
        while :; do "$0" tick >/dev/null 2>&1 || true; sleep 30; done;;
    status)
        echo 'Recovery: 3 consecutive failures, at least 300s between restarts, at most 3/hour'
        [ ! -f "$state" ] || cat "$state"
        [ ! -f "$snapshot" ] || cat "$snapshot";;
    *) echo 'Usage: ufi_dns_health.sh [status|tick|watch]' >&2; exit 2;;
esac
