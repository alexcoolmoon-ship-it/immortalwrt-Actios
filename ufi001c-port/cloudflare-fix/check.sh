#!/bin/sh
set -u
export LC_ALL=C
echo 'UFI001C CLOUDFLARE PATCH CHECK 1.0'
[ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = thwc,ufi001c ] || exit 2
cat /etc/ufi001c-release
printf 'BOOT_ID='; cat /proc/sys/kernel/random/boot_id
if grep -q UFI001C_CLOUDFLARE_PATCH_V1 /usr/bin/podkop; then
    echo PATCH=INSTALLED
else
    echo PATCH=NOT_INSTALLED
    exit 2
fi
status=0
if sh /usr/bin/podkop cloudflare_check; then echo CLOUDFLARE_DOH_PROXY=PASS; else echo CLOUDFLARE_DOH_PROXY=FAIL; status=3; fi
for addr in 127.0.0.42 127.0.0.1; do
    answer=$(dig @"$addr" example.com A +time=4 +tries=1 +noall +comments +answer 2>/dev/null) || answer=''
    if printf '%s\n' "$answer" | grep -q 'status: NOERROR,' &&
       printf '%s\n' "$answer" | grep -Eq '[[:space:]]IN[[:space:]]+A[[:space:]]+[0-9]+\.'; then
        echo "DNS_$addr=PASS"
    else echo "DNS_$addr=FAIL"; status=3; fi
    answer=$(dig @"$addr" fakeip.podkop.fyi A +time=4 +tries=1 +short 2>/dev/null) || answer=''
    if printf '%s\n' "$answer" | grep -Eq '^198\.(18|19)\.'; then
        echo "FAKEIP_$addr=PASS"
    else echo "FAKEIP_$addr=FAIL"; status=3; fi
done
jq '{dns_servers:[.dns.servers[]? | {tag,type,server,server_port,detour}],
    proxy_resolvers:[.outbounds[]? | select(.server != null) | {tag,type,domain_resolver}],
    fakeip_cache:.experimental.cache_file.store_fakeip}' /etc/sing-box/config.json
echo UFI_CLOUDFLARE_CHECK_COMPLETE
exit "$status"
