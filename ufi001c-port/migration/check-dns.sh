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
BOOTSTRAP_SRV=$(uci -q get podkop.settings.bootstrap_dns_server 2>/dev/null || true)
[ -n "$BOOTSTRAP_SRV" ] || BOOTSTRAP_SRV=77.88.8.8

report "=== UFI001C DNS check ==="
report "Bootstrap: $BOOTSTRAP_SRV ($TRANSPORT)"

# 1. Bootstrap. Deliberately direct: the bootstrap must work standalone,
#    it is what resolves everything else when the tunnel is down.
if [ "$TRANSPORT" = tcp ]; then
    # No dig on the device; for TCP check that the server accepts connections.
    if command -v nc >/dev/null 2>&1 && nc -z -w 5 "$BOOTSTRAP_SRV" 53 >/dev/null 2>&1; then
        ok "Bootstrap $BOOTSTRAP_SRV TCP/53 reachable"
    else
        fail "Bootstrap $BOOTSTRAP_SRV TCP/53 unreachable"
    fi
else
    if command -v nslookup >/dev/null 2>&1 &&
        nslookup -timeout=5 example.com "$BOOTSTRAP_SRV" >/dev/null 2>&1; then
        ok "Bootstrap $BOOTSTRAP_SRV UDP/53 resolves example.com"
    else
        fail "Bootstrap $BOOTSTRAP_SRV UDP/53 did not resolve example.com"
    fi
fi

# 2. Cloudflare DoH through the configured proxy.
# Clean profile: `podkop cloudflare_status`. Legacy overlay: ufi_dns_tunnel.sh.
if command -v /usr/bin/podkop >/dev/null 2>&1; then
    cf_json=$(/usr/bin/podkop cloudflare_status 2>/dev/null || true)
    cf_mode=$(printf '%s' "$cf_json" | jq -r '.mode // ""' 2>/dev/null || true)
    cf_ok=$(printf '%s' "$cf_json" | jq -r '.dns_status // 0' 2>/dev/null || true)
    if [ "$cf_mode" = cloudflare_proxy ] && [ "$cf_ok" = 1 ]; then
        ok "Cloudflare DoH through proxy"
    elif [ "$cf_mode" = cloudflare_proxy ]; then
        fail "Cloudflare DoH through proxy did not answer"
    else
        report "SKIP: Cloudflare patch not enabled (mode=$cf_mode)"
    fi
elif [ -x /usr/lib/podkop/ufi_dns_tunnel.sh ] &&
    sh /usr/lib/podkop/ufi_dns_tunnel.sh probe-dns >/dev/null 2>&1; then
    ok "Cloudflare DoH through proxy tunnel"
else
    fail "Cloudflare DoH through proxy tunnel"
fi

# 3. Local FakeIP stack (via `podkop check_fakeip`, superset JSON).
if command -v /usr/bin/podkop >/dev/null 2>&1; then
    fakeip_report=$(/usr/bin/podkop check_fakeip 2>/dev/null || true)
    if printf '%s' "$fakeip_report" | jq -e '.local_fakeip and .dnsmasq_fakeip and .policy_route' >/dev/null 2>&1; then
        ok "FakeIP (sing-box, dnsmasq, policy route)"
    else
        fail "FakeIP check (sing-box/dnsmasq/policy route)"
    fi
else
    fail "podkop not found"
fi

report "=== Result: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
