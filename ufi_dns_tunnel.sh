#!/bin/sh
# UFI001C / Podkop 0.7.23: DNS via one existing proxy outbound, v3.1.2.
# MIT; local backups can contain private proxy credentials (mode 0600).
set -eu
umask 077

die() { echo "ERROR: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Required command: $1"; }
setting() { uci -q get "podkop.settings.$1" 2>/dev/null || true; }

filter_config() {
    section=${1:-$(setting ufi_dns_proxy_section)}
    [ -n "$section" ] || { cat; return; }
    if [ "$section" = '@auto' ]; then
        section=$(sh /usr/lib/podkop/ufi_startup.sh resolve) || die "No configured proxy section for DNS"
    fi
    case "$section" in *[!A-Za-z0-9_]*|'') die "Invalid section name";; esac
    jq -e --arg tag "$section-out" '
        if ([.outbounds[]? | select(.tag == $tag)] | length) != 1 then
            error("DNS proxy outbound is missing or duplicated")
        elif ([.outbounds[] | select(.tag == $tag and
            (.type == "vless" or .type == "trojan" or .type == "shadowsocks" or
             .type == "vmess" or .type == "socks" or .type == "http" or
             .type == "hysteria2" or .type == "tuic" or .type == "anytls") and
            ((.server // "") | length > 0) and ((.detour // "") == ""))] | length) != 1 then
            error("Choose one proxy URL; direct, VPN, selector and chained outbounds are not supported by this hotfix")
        elif ([.dns.servers[]? | select(.tag == "dns-server")] | length) != 1 or
             ([.dns.servers[]? | select(.tag == "bootstrap-dns-server")] | length) != 1 then
            error("Unexpected Podkop DNS configuration")
        else
            (.dns.servers[] | select(.tag == "dns-server")) |=
                (.detour = $tag | del(.domain_resolver, .bind_interface)) |
            (.dns.servers[] | select(.tag == "bootstrap-dns-server")) |=
                del(.detour, .domain_resolver) |
            (.outbounds[] | select(.tag == $tag)).domain_resolver =
                {server: "bootstrap-dns-server", strategy: "ipv4_only"} |
            if .route.rule_set then
                .route.rule_set |= map(if .type == "remote" then .download_detour = $tag else . end)
            else . end
        end'
}

policy_status() {
    sh /usr/lib/podkop/ufi_policy_routing.sh check
}

probe_tunnel_dns() (
    # A subshell confines positional parameters, traps and temporary files.
    section=$(setting ufi_dns_proxy_section)
    [ -n "$section" ] || die "DNS tunnel section is not configured"
    conf=$(setting config_path)
    conf=${conf:-/etc/sing-box/config.json}
    section=$(jq -r '[.dns.servers[]? | select(.tag == "dns-server") | .detour // empty][0] // empty' "$conf")
    case "$section" in *-out) section=${section%-out} ;; *) die "Start Podkop with a configured proxy section first" ;; esac
    # Port 4534 must use the SAME outbound as the actual DNS server.
    jq -e --arg tag "$section-out" '
        any(.dns.servers[]?; .tag == "dns-server" and .type == "https" and .detour == $tag) and
        any(.inbounds[]?; .tag == "service-mixed-in" and .type == "mixed" and
            .listen == "127.0.0.1" and .listen_port == 4534) and
        any(.route.rules[]?; .outbound == $tag and
            (((.inbound // []) | index("service-mixed-in")) != null))
    ' "$conf" >/dev/null || die "Generated DNS tunnel and service proxy do not match"
    server=$(jq -r '.dns.servers[] | select(.tag == "dns-server") | .server' "$conf")
    port=$(jq -r '.dns.servers[] | select(.tag == "dns-server") | .server_port // 443' "$conf")
    path=$(jq -r '.dns.servers[] | select(.tag == "dns-server") | .path // "/dns-query"' "$conf")
    case "$server" in *:*) server="[$server]";; esac
    case "$path" in /*) ;; *) die "Unexpected DoH path";; esac
    tmp=$(mktemp -d /tmp/ufi-doh-check.XXXXXX)
    trap 'rm -rf "$tmp"' EXIT HUP INT TERM
    # RFC 8484 DNS wire format, example.com A. No provider-specific JSON API.
    printf '\000\000\001\000\000\001\000\000\000\000\000\000\007example\003com\000\000\001\000\001' > "$tmp/query"
    curl --noproxy '' -fsS --connect-timeout 5 --max-time 10 \
        --proxy http://127.0.0.1:4534 \
        -H 'Content-Type: application/dns-message' -H 'Accept: application/dns-message' \
        --data-binary "@$tmp/query" "https://$server:$port$path" > "$tmp/answer" ||
        die "DoH request through the configured proxy failed"
    set -- $(od -An -tu1 -N12 "$tmp/answer")
    [ "$#" -eq 12 ] || die "Truncated DNS response"
    [ "$1" -eq 0 ] && [ "$2" -eq 0 ] && [ "$(($3 & 128))" -ne 0 ] &&
        [ "$(($3 & 2))" -eq 0 ] && [ "$(($4 & 15))" -eq 0 ] &&
        [ "$(($7 * 256 + $8))" -gt 0 ] || die "DoH returned a DNS error or empty answer"
)

check_status() {
    status=0
    [ ! -r /etc/ufi001c-release ] || cat /etc/ufi001c-release
    conf=$(setting config_path)
    conf=${conf:-/etc/sing-box/config.json}
    section=$(setting ufi_dns_proxy_section)
    echo "DNS tunnel preference: ${section:-not installed}"
    actual=$(jq -r '[.dns.servers[]? | select(.tag == "dns-server") | .detour // empty][0] // empty' "$conf" 2>/dev/null || true)
    echo "Active DNS outbound: ${actual:-Podkop is not configured/running}"
    sing-box version | head -n 1
    if [ -r "$conf" ]; then
        jq '{dns_servers: [.dns.servers[]? | {tag,type,server,detour}],
             proxy_bootstrap: [.outbounds[]? | select(.domain_resolver != null) | {tag,domain_resolver}]}' "$conf"
    fi
    policy_status || status=1
    if probe_tunnel_dns; then
        echo "DoH through proxy: PASS"
    else
        echo "DoH through proxy: FAIL"
        status=1
    fi
    if command -v dig >/dev/null 2>&1; then
        echo "Router DNS, example.com:"
        dig @127.0.0.42 example.com A +time=5 +tries=1 +noall +answer || status=1
    fi
    echo 'Local FakeIP checks (public test availability is separate):'
    fakeip_report=$(sh /usr/lib/podkop/ufi_fakeip_check.sh local) || fakeip_report='{}'
    printf '%s\n' "$fakeip_report"
    printf '%s' "$fakeip_report" | jq -e '.local_fakeip and .dnsmasq_fakeip and .policy_route' >/dev/null || status=1
    return "$status"
}

restore_backup() {
    backup=$1
    case "$backup" in /root/ufi-dns-tunnel-backup.*) ;; *) die "Unexpected backup path";; esac
    [ -f "$backup/podkop.program" ] && [ -f "$backup/podkop.config" ] || die "Incomplete backup"
    cp "$backup/podkop.program" /usr/bin/podkop
    chmod 755 /usr/bin/podkop
    cp "$backup/podkop.config" /etc/config/podkop
    chmod 600 /etc/config/podkop
    uci -q revert podkop || true
    if [ -f "$backup/sing-box.config" ]; then
        cp "$backup/sing-box.config" /etc/config/sing-box
        chmod 600 /etc/config/sing-box
        uci -q revert sing-box || true
        /etc/init.d/podkop-engine restart
    fi
    if [ -f "$backup/sing-box.json" ]; then
        conf=$(cat "$backup/config-path")
        cp "$backup/sing-box.json" "$conf"
        chmod 600 "$conf"
    fi
    /etc/init.d/podkop restart
    echo "Restored Podkop settings from $backup"
}

install_fix() {
    [ "$(id -u)" = 0 ] || die "Run as root on the modem"
    for tool in uci jq curl sing-box awk sha256sum; do need "$tool"; done
    section=${1:-@auto}
    section=$(sh /usr/lib/podkop/ufi_startup.sh resolve "$section") || die "Add a proxy section and enter a URL in LuCI first"
    case "$section" in *[!A-Za-z0-9_]*|'') die "Invalid section name";; esac
    [ "$(uci -q get "podkop.$section.connection_type" || true)" = proxy ] || die "Section $section is not a proxy"
    [ -z "$(uci changes podkop)" ] || die "Save or discard pending Podkop changes in LuCI first"
    [ -z "$(uci changes sing-box)" ] || die "Save or discard pending sing-box changes first"
    [ -f /etc/config/sing-box ] || die "Missing engine service configuration"
    conf=$(setting config_path)
    conf=${conf:-/etc/sing-box/config.json}
    [ -s "$conf" ] || die "Start Podkop once to generate its configuration"
    jq -e '(.dns.servers | type == "array") and (.outbounds | type == "array")' "$conf" >/dev/null 2>&1 ||
        die "Start Podkop with a configured proxy URL first; the initial sing-box template has no DNS configuration"
    prog=/usr/bin/podkop
    helper=/usr/lib/podkop/ufi_dns_tunnel.sh
    expected=a23ac5e1644bf90465e273253d8edacf487b6db3356556a3d138114887f5aeb1
    if ! grep -q 'UFI_DNS_TUNNEL_V1' "$prog"; then
        [ "$(sha256sum "$prog" | awk '{print $1}')" = "$expected" ] ||
            die "Different Podkop program version; no changes made. Send its version and sha256sum."
    fi
    tempdir=$(mktemp -d /tmp/ufi-dns-tunnel.XXXXXX)
    probe_pid=''
    trap '[ -z "$probe_pid" ] || { kill "$probe_pid" 2>/dev/null || true; wait "$probe_pid" 2>/dev/null || true; }; rm -rf "$tempdir"' EXIT HUP INT TERM

    # Check the exact existing tunnel with direct bootstrap before changing UCI.
    jq '(.dns.servers[] | select(.tag == "dns-server")) =
        {tag:"dns-server",type:"https",server:"1.1.1.1",server_port:443,path:"/dns-query"} |
        (.dns.servers[] | select(.tag == "bootstrap-dns-server")) =
        {tag:"bootstrap-dns-server",type:"udp",server:"77.88.8.8",server_port:53}' "$conf" |
        filter_config "$section" > "$tempdir/candidate.json"
    sing-box check -c "$tempdir/candidate.json" >/dev/null || die "Config validation failed; no changes made"
    jq --arg tag "$section-out" '{
        log:{level:"error"},
        dns:{servers:.dns.servers,final:"dns-server",strategy:"ipv4_only"},
        inbounds:[{type:"mixed",tag:"ufi-probe",listen:"127.0.0.1",listen_port:18453}],
        outbounds:.outbounds,
        route:{final:$tag,auto_detect_interface:true,default_domain_resolver:"bootstrap-dns-server"}
    }' "$tempdir/candidate.json" > "$tempdir/probe.json"
    sing-box check -c "$tempdir/probe.json" >/dev/null || die "Probe config validation failed"
    sing-box run -c "$tempdir/probe.json" >"$tempdir/probe.log" 2>&1 &
    probe_pid=$!
    sleep 1
    kill -0 "$probe_pid" 2>/dev/null || die "Temporary tunnel process did not start; no changes made"
    echo "Checking existing tunnel and Cloudflare DoH (up to 20 seconds)..."
    if ! curl --noproxy '' -fsS --connect-timeout 8 --max-time 20 \
        --proxy http://127.0.0.1:18453 -H 'accept: application/dns-json' \
        'https://1.1.1.1/dns-query?name=example.com&type=A' > "$tempdir/answer.json" ||
        ! jq -e '.Status == 0 and ((.Answer // []) | length > 0)' "$tempdir/answer.json" >/dev/null; then
        cp "$tempdir/probe.log" /root/ufi-dns-tunnel-probe.log
        die "Existing tunnel/DoH probe failed; no settings changed. Private log: /root/ufi-dns-tunnel-probe.log"
    fi
    kill "$probe_pid" 2>/dev/null || true
    wait "$probe_pid" 2>/dev/null || true
    probe_pid=''

    if grep -q 'UFI_DNS_TUNNEL_V1' "$prog"; then
        cp "$prog" "$tempdir/podkop"
    else
        awk '
          /^    sing_box_save_config$/ {
            print "    # UFI_DNS_TUNNEL_V1: preserve DNS routing across reload/reboot"
            print "    config=$(printf '\''%s\\n'\'' \"$config\" | sh /usr/lib/podkop/ufi_dns_tunnel.sh filter) || {"
            print "        log \"DNS tunnel configuration rejected\" \"fatal\""
            print "        exit 1"
            print "    }"
            count++
          }
          {print}
          END {if (count != 1) exit 1}
        ' "$prog" > "$tempdir/podkop" || die "Unexpected config generator; no changes made"
    fi
    sh -n "$tempdir/podkop" || die "Program syntax check failed"
    backup=$(mktemp -d /root/ufi-dns-tunnel-backup.XXXXXX)
    cp "$prog" "$backup/podkop.program"
    cp /etc/config/podkop "$backup/podkop.config"
    cp /etc/config/sing-box "$backup/sing-box.config"
    cp "$conf" "$backup/sing-box.json"
    printf '%s\n' "$conf" > "$backup/config-path"
    printf '%s\n' "$backup" > /root/ufi-dns-tunnel-last-backup
    if [ "$(readlink -f "$0")" != "$helper" ]; then cp "$0" "$helper"; fi
    chmod 755 "$helper"
    cp "$tempdir/podkop" "$prog"
    chmod 755 "$prog"
    if ! uci batch <<EOF
set podkop.settings.ufi_dns_proxy_section='$section'
set podkop.settings.dns_type='doh'
set podkop.settings.dns_server='1.1.1.1'
set podkop.settings.bootstrap_dns_server='77.88.8.8'
set podkop.settings.download_lists_via_proxy='1'
set podkop.settings.download_lists_via_proxy_section='$section'
set sing-box.main.dns_fallback='0'
set sing-box.main.failsafe='0'
commit sing-box
commit podkop
EOF
    then
        restore_backup "$backup"
        die "UCI update failed; previous settings restored"
    fi
    /etc/init.d/podkop-engine restart || { restore_backup "$backup"; die "Engine policy update failed; restored backup"; }
    /etc/init.d/podkop restart || { restore_backup "$backup"; die "Restart failed; restored backup"; }
    applied=0
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        if jq -e --arg tag "$section-out" 'any(.dns.servers[]?; .tag == "dns-server" and .detour == $tag)' "$conf" >/dev/null 2>&1 &&
            pidof sing-box >/dev/null 2>&1; then applied=1; break; fi
        sleep 1
    done
    if [ "$applied" != 1 ]; then
        restore_backup "$backup"
        die "Podkop did not apply the DNS tunnel; previous settings restored"
    fi
    echo "DNS tunnel installed. Bootstrap is used only to reach the proxy endpoint."
    echo "Backup: $backup"
    echo "Rollback: sh /usr/lib/podkop/ufi_dns_tunnel.sh rollback"
    check_status
}

case "${1:-install}" in
    filter) filter_config "${2:-}" ;;
    probe-dns) probe_tunnel_dns ;;
    install) install_fix "${2:-@auto}" ;;
    check) check_status ;;
    rollback) [ -r /root/ufi-dns-tunnel-last-backup ] || die "No backup recorded"; restore_backup "$(cat /root/ufi-dns-tunnel-last-backup)" ;;
    *) die "Usage: sh ufi-dns-tunnel.sh [install [section] | check | probe-dns | rollback]" ;;
esac
