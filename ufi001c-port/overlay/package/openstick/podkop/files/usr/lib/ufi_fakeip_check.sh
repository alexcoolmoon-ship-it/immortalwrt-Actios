#!/bin/sh
# Read-only diagnostics. An unavailable public test is not a negative FakeIP result.
set -eu
domain=fakeip.podkop.fyi
case "${1:-full}" in full|local) ;; *) echo 'Usage: ufi_fakeip_check.sh [full|local]' >&2; exit 1 ;; esac

fakeip_address() {
    # Validate all four octets; 198.18.0.0/15 includes 198.19.* too.
    awk -F. 'NF == 4 && $1 == 198 && ($2 == 18 || $2 == 19) {
        valid=1
        for (i=1; i<=4; i++) if ($i !~ /^[0-9]+$/ || $i+0 > 255) valid=0
        if (valid) { print; exit }
    }'
}

answer=$(dig +time=2 +tries=1 +short @127.0.0.42 "$domain" A 2>/dev/null | fakeip_address) || answer=''
router_answer=$(dig +time=2 +tries=1 +short @127.0.0.1 "$domain" A 2>/dev/null | fakeip_address) || router_answer=''
local_ok=false
router_ok=false
route_ok=false
[ -z "$answer" ] || local_ok=true
[ -z "$router_answer" ] || router_ok=true
if sh /usr/lib/podkop/ufi_policy_routing.sh check >/dev/null 2>&1; then route_ok=true; fi

external_available=false
external_fakeip=null
external_status=not_run
if [ "${1:-full}" = full ]; then
    external_status=unavailable
    # Keep the upstream test URL and route; never change its route to force a pass.
    response=$(curl --noproxy '*' --connect-timeout 2 --max-time 3 -fsS "https://$domain/check" 2>/dev/null) || response=''
    if printf '%s' "$response" | jq -e 'type == "object" and (.fakeip | type == "boolean")' >/dev/null 2>&1; then
        external_available=true
        external_status=completed
        external_fakeip=$(printf '%s' "$response" | jq -r '.fakeip')
    fi
fi

jq -n --argjson local_fakeip "$local_ok" --argjson dnsmasq_fakeip "$router_ok" \
    --argjson policy_route "$route_ok" --arg singbox_answer "$answer" --arg router_answer "$router_answer" \
    --argjson external_available "$external_available" --arg external_status "$external_status" \
    --argjson fakeip "$external_fakeip" \
    '{version:1, local_fakeip:$local_fakeip, dnsmasq_fakeip:$dnsmasq_fakeip,
      policy_route:$policy_route, singbox_answer:$singbox_answer, router_answer:$router_answer,
      external_available:$external_available, external_status:$external_status, fakeip:$fakeip}'
