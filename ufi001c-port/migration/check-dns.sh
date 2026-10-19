#!/bin/sh
# UFI001C DNS diagnostic. Runs ON the modem (piped via ssh from CHECK-DNS.cmd).
# Checks, in order:
#   1. Yandex bootstrap DNS over the configured transport (podkop ufi_bootstrap_transport, tcp|udp)
#   2. Cloudflare DoH through the configured proxy tunnel
#   3. Local FakeIP (sing-box, dnsmasq, policy routing)
# Prints a human-readable report to stdout. Exit code 0 if all checks pass.
set -u

PASS=0
FAIL=0
report() { printf '%s\n' "$*"; }
ok() { PASS=$((PASS + 1)); report "PASS: $*"; }
fail() { FAIL=$((FAIL + 1)); report "FAIL: $*"; }

TRANSPORT=$(uci -q get podkop.settings.ufi_bootstrap_transport 2>/dev/null || true)
case "$TRANSPORT" in tcp | udp) ;; *) TRANSPORT=tcp ;; esac

report "=== UFI001C DNS check ==="
report "Yandex bootstrap transport: $TRANSPORT (server 77.88.8.8)"

# 1. Yandex bootstrap. Deliberately direct: the bootstrap must work standalone,
#    it is what resolves everything else when the tunnel is down.
if [ "$TRANSPORT" = tcp ]; then
    # No dig on the device; for TCP check that 77.88.8.8:53 accepts connections.
    if command -v nc >/dev/null 2>&1 && nc -z -w 5 77.88.8.8 53 >/dev/null 2>&1; then
        ok "Yandex 77.88.8.8 TCP/53 reachable"
    else
        fail "Yandex 77.88.8.8 TCP/53 unreachable"
    fi
else
    if command -v nslookup >/dev/null 2>&1 &&
        nslookup -timeout=5 example.com 77.88.8.8 >/dev/null 2>&1; then
        ok "Yandex 77.88.8.8 UDP/53 resolves example.com"
    else
        fail "Yandex 77.88.8.8 UDP/53 did not resolve example.com"
    fi
fi

# 2. Cloudflare DoH through the configured proxy tunnel.
if [ -x /usr/lib/podkop/ufi_dns_tunnel.sh ] &&
    sh /usr/lib/podkop/ufi_dns_tunnel.sh probe-dns >/dev/null 2>&1; then
    ok "Cloudflare DoH through proxy tunnel"
else
    fail "Cloudflare DoH through proxy tunnel"
fi

# 3. Local FakeIP stack.
if [ -x /usr/lib/podkop/ufi_fakeip_check.sh ]; then
    fakeip_report=$(sh /usr/lib/podkop/ufi_fakeip_check.sh local 2>/dev/null || true)
    if printf '%s' "$fakeip_report" | jq -e '.local_fakeip and .dnsmasq_fakeip and .policy_route' >/dev/null 2>&1; then
        ok "FakeIP (sing-box, dnsmasq, policy route)"
    else
        fail "FakeIP check (sing-box/dnsmasq/policy route)"
    fi
else
    fail "ufi_fakeip_check.sh not found"
fi

report "=== Result: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
