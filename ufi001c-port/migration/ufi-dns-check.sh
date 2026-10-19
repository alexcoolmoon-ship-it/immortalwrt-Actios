#!/bin/sh
# UFI001C DNS triage 1.0: read-only probes, no service/config/radio changes.
set -u
umask 077
export LC_ALL=C

dns_probe() {
    # Hostnames and returned proxy IPs stay out of the report.
    local label=$1 resolver=$2 name=$3 proto=$4 answer rc=0 result=FAIL
    if [ "$proto" = TCP ]; then
        answer=$(dig @"$resolver" "$name" A +tcp +time=2 +tries=1 +noall +comments +answer 2>/dev/null) || rc=$?
    else
        answer=$(dig @"$resolver" "$name" A +notcp +ignore +time=2 +tries=1 +noall +comments +answer 2>/dev/null) || rc=$?
    fi
    if [ "$rc" = 0 ] && printf '%s\n' "$answer" | grep -q 'status: NOERROR,' &&
       printf '%s\n' "$answer" | grep -Eq '[[:space:]]IN[[:space:]]+A[[:space:]]+[0-9]+\.'; then
        result=PASS
    fi
    printf '%s=%s exit=%s\n' "$label" "$result" "$rc"
    if printf '%s\n' "$answer" | grep -q 'status: SERVFAIL,'; then
        printf '%s_RCODE=SERVFAIL\n' "$label"
    elif printf '%s\n' "$answer" | grep -q 'status: REFUSED,'; then
        printf '%s_RCODE=REFUSED\n' "$label"
    elif printf '%s\n' "$answer" | grep -q 'status: NXDOMAIN,'; then
        printf '%s_RCODE=NXDOMAIN\n' "$label"
    fi
    if [ "$name" = fakeip.podkop.fyi ]; then
        if printf '%s\n' "$answer" | grep -Eq '[[:space:]]IN[[:space:]]+A[[:space:]]+198\.(18|19)\.'; then
            printf 'FAKEIP_%s=PASS\n' "$resolver"
        else printf 'FAKEIP_%s=FAIL\n' "$resolver"; fi
    fi
}

doh_probe() {
    local rc=0 status=FAIL
    # Only probe the existing loopback listener bound to the DNS outbound.
    if ! jq -e '
        [.dns.servers[]? | select(.tag=="dns-server")] as $d |
        ($d|length)==1 and $d[0].type=="https" and $d[0].server=="1.1.1.1" and
        ($d[0].server_port // 443)==443 and $d[0].path=="/dns-query" and ($d[0].detour // "")!="" and
        any(.inbounds[]?; .tag=="service-mixed-in" and .listen=="127.0.0.1" and .listen_port==4534) and
        any(.route.rules[]?; .outbound==$d[0].detour and ((.inbound // [])|index("service-mixed-in"))!=null)
    ' "$conf" >/dev/null 2>&1; then
        echo CLOUDFLARE_CONFIG=NOT_MATCHING
        return 0
    fi
    echo CLOUDFLARE_CONFIG=MATCHING
    printf '\000\000\001\000\000\001\000\000\000\000\000\000\007example\003com\000\000\001\000\001' >"$work/query"
    curl --noproxy '' --proxy http://127.0.0.1:4534 --fail --silent --show-error \
        --connect-timeout 4 --max-time 12 --max-filesize 65536 \
        -H 'Content-Type: application/dns-message' -H 'Accept: application/dns-message' \
        --data-binary "@$work/query" -o "$work/answer" \
        -w 'CURL_HTTP=%{http_code}\nCURL_CONNECT_HTTP=%{http_connect}\nCURL_CONNECT_SECONDS=%{time_connect}\nCURL_TOTAL_SECONDS=%{time_total}\n' \
        https://1.1.1.1/dns-query >"$work/metrics" 2>"$work/curl-error" || rc=$?
    printf 'CURL_EXIT=%s\n' "$rc"
    cat "$work/metrics"
    # The command only knows public Cloudflare + loopback; no proxy credentials.
    head -c 2048 "$work/curl-error"
    printf '\n'
    if [ "$rc" = 0 ] && [ -f "$work/answer" ]; then
        set -- $(hexdump -v -n 12 -e '1/1 "%u "' "$work/answer")
        if [ "$#" = 12 ]; then
            printf 'DNS_RCODE=%s DNS_ANSWERS=%s\n' "$(($4 & 15))" "$(($7 * 256 + $8))"
            if [ "$1" = 0 ] && [ "$2" = 0 ] && [ "$(($3 & 128))" != 0 ] &&
               [ "$(($3 & 2))" = 0 ] && [ "$(($4 & 15))" = 0 ] &&
               [ "$(($5 * 256 + $6))" = 1 ] && [ "$(($7 * 256 + $8))" -gt 0 ] &&
               [ "$(wc -c <"$work/answer")" -gt 33 ]; then status=PASS; fi
        fi
    fi
    printf 'CLOUDFLARE_DOH_PROXY=%s\n' "$status"
}

main() (
    [ "$(id -u)" = 0 ] || { echo ERROR=ROOT_REQUIRED; exit 2; }
    [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = thwc,ufi001c ] || { echo ERROR=WRONG_BOARD; exit 2; }
    for command in jq dig curl hexdump; do
        command -v "$command" >/dev/null 2>&1 || { printf 'ERROR=MISSING_%s\n' "$command"; exit 2; }
    done
    work=$(mktemp -d /tmp/ufi-dns-triage.XXXXXX) || exit 2
    trap 'rm -rf "$work"' EXIT HUP INT TERM
    echo 'UFI001C DNS TRIAGE 1.0'
    cat /etc/ufi001c-release 2>/dev/null
    printf 'UTC='; date -u '+%Y-%m-%dT%H:%M:%SZ'
    printf 'BOOT_ID='; cat /proc/sys/kernel/random/boot_id
    printf 'UPTIME='; cut -d' ' -f1 /proc/uptime
    awk '/^(MemTotal|MemAvailable|MemFree):/' /proc/meminfo
    printf 'CLOUDFLARE_MODE='; uci -q get podkop.settings.cloudflare_dns_patch 2>/dev/null || echo unset
    printf 'SING_BOX_PROCESSES='; pgrep -x sing-box 2>/dev/null | wc -l

    echo '[LTE]'
    if ubus -t 5 call network.interface.modem status >"$work/lte" 2>/dev/null; then
        jq '{up,pending,available,proto,l3_device}' "$work/lte" 2>/dev/null
        dev=$(jq -er '.l3_device | select(type=="string" and length>0)' "$work/lte" 2>/dev/null) || dev=''
        case "$dev" in ''|*[!a-zA-Z0-9_.:-]*) echo LTE_KERNEL_DEVICE=UNAVAILABLE ;;
        *)
            ip -j link show dev "$dev" 2>/dev/null | jq -c '{link_up:any(.[]; ((.flags // [])|index("UP"))!=null)}'
            ip -j -4 address show dev "$dev" 2>/dev/null | jq -c '{ipv4:any(.[].addr_info[]?; .family=="inet" and .scope=="global")}'
            ;;
        esac
    else echo LTE_STATUS=UNAVAILABLE; fi
    route=$(ip -4 route get 77.88.8.8 2>/dev/null) || route=''
    route_dev=$(printf '%s\n' "$route" | awk '{for(i=1;i<NF;i++) if($i=="dev") print $(i+1)}')
    printf 'YANDEX_ROUTE_DEVICE=%s\n' "${route_dev:-UNAVAILABLE}"

    echo '[YANDEX BOOTSTRAP]'
    for domain in example.com google.com; do
        dns_probe "YANDEX_${domain}_TCP" 77.88.8.8 "$domain" TCP
        dns_probe "YANDEX_${domain}_UDP" 77.88.8.8 "$domain" UDP
    done

    conf=$(uci -q get podkop.settings.config_path 2>/dev/null) || conf=''
    conf=${conf:-/etc/sing-box/config.json}
    echo '[RUNNING CONFIG]'
    if jq -e 'type=="object"' "$conf" >/dev/null 2>&1; then
        jq '{dns_servers:[.dns.servers[]? |
              select(.tag=="dns-server" or .tag=="bootstrap-dns-server" or .tag=="fakeip-server") |
              {tag,type,server:(if .server=="1.1.1.1" or .server=="77.88.8.8" then .server else null end),server_port,detour}],
            proxy_resolvers:[.outbounds[]? | select(.server!=null) |
              {type,bootstrap_resolver:(if (.domain_resolver|type)=="object" then
                .domain_resolver.server=="bootstrap-dns-server" else .domain_resolver=="bootstrap-dns-server" end)}],
            fakeip_cache:.experimental.cache_file.store_fakeip}' "$conf"
        jq -r '[.outbounds[]? | .server? | select(type=="string" and length>0)] | unique | .[0:8][]' "$conf" >"$work/hosts"
        n=0
        while IFS= read -r host; do
            n=$((n+1))
            case "$host" in *:*) printf 'PROXY_%s_BOOTSTRAP=IP_LITERAL\n' "$n" ;;
            *[!a-zA-Z0-9_.-]*) printf 'PROXY_%s_BOOTSTRAP=UNSUPPORTED_NAME\n' "$n" ;;
            *) if printf '%s\n' "$host" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
                   printf 'PROXY_%s_BOOTSTRAP=IP_LITERAL\n' "$n"
               else
                   dns_probe "PROXY_${n}_YANDEX_TCP" 77.88.8.8 "$host" TCP
                   dns_probe "PROXY_${n}_YANDEX_UDP" 77.88.8.8 "$host" UDP
               fi ;;
            esac
        done <"$work/hosts"
        echo '[CLOUDFLARE THROUGH EXISTING PROXY]'
        doh_probe
    else echo RUNNING_CONFIG=INVALID_OR_UNAVAILABLE; fi

    echo '[LOCAL DNS AND FAKEIP]'
    for resolver in 127.0.0.42 127.0.0.1; do
        dns_probe "LOCAL_${resolver}" "$resolver" example.com UDP
        dns_probe "LOCAL_FAKEIP_${resolver}" "$resolver" fakeip.podkop.fyi UDP
    done
    echo '[RECENT KERNEL RF/OOM MATCHES FROM THIS BOOT]'
    dmesg 2>/dev/null | grep -E 'rflte_|remoteproc.*(crash|fatal|recover)|[Oo]ut of memory|oom-kill|Killed process.*sing-box|[Kk]ernel panic|Oops:' | tail -n 30
    echo '[SING-BOX LOG COUNTS: LAST 200 MATCHING LINES]'
    logread -e sing-box 2>/dev/null | tail -n 200 | awk '
        {s=tolower($0); if(s~/lookup|dns:|resolve/) dns++; if(s~/tls|certificate|x509/) tls++;
         if(s~/timeout|deadline exceeded/) timeout++}
        END{printf "LOG_DNS=%d LOG_TLS=%d LOG_TIMEOUT=%d\n",dns,tls,timeout}'
    echo 'UFI_DNS_TRIAGE_COMPLETE (collection complete; see individual probe results)'
    exit 0
)

main "$@"
