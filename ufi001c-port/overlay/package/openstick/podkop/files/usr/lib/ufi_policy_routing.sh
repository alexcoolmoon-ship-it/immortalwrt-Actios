#!/bin/sh
# UFI001C: require functioning IPv4 policy routing before enabling TProxy.
set -eu

fail() { echo "IPv4 Podkop routing: $*" >&2; exit 1; }

kernel_ready() {
    if [ -r /proc/config.gz ]; then
        flags=$(zcat /proc/config.gz | grep -E '^CONFIG_(IP_ADVANCED_ROUTER|IP_MULTIPLE_TABLES|FIB_RULES)=y$' || true)
        for flag in IP_ADVANCED_ROUTER IP_MULTIPLE_TABLES FIB_RULES; do
            printf '%s\n' "$flags" | grep -qx "CONFIG_$flag=y" ||
                fail "CONFIG_$flag is disabled; install the corrected kernel."
        done
    fi
    rules=$(ip -4 rule show) || fail "cannot read IPv4 rules."
    [ -n "$rules" ] || fail "empty rule dump; exit code 0 does not prove kernel support."
}

has_rule() {
    printf '%s\n' "$rules" | grep -Eq '^105:[[:space:]]+from all fwmark 0x0*100000/0x0*100000 lookup (podkop|105)( |$)'
}

check_routes() {
    kernel_ready
    has_rule || fail "required fwmark rule at priority 105 is missing."
    ip -4 route show table 105 | grep -Eq '^local (default|0\.0\.0\.0/0) dev lo( |$)' ||
        fail "local default route in table 105 is missing."
    lookup=$(ip -4 route get 198.18.0.254 mark 0x100000) ||
        fail "marked route lookup failed; ip-full and the corrected kernel are required."
    printf '%s\n' "$lookup" | grep -Eq '^local 198\.18\.0\.254 .*dev lo( |$)' ||
        fail "marked traffic is not routed locally."
    echo "IPv4 Podkop routing: rule and local route installed"
}

case "${1:-check}" in
    ensure)
        # Test before touching table 105: old kernels alias arbitrary tables to main.
        kernel_ready
        ip -4 route replace local 0.0.0.0/0 dev lo table 105 || fail "cannot install the local route."
        if ! has_rule; then
            ip -4 rule add fwmark 0x100000/0x100000 table 105 priority 105 || fail "cannot install the fwmark rule."
        fi
        check_routes
        ;;
    check) check_routes ;;
    *) fail "usage: sh ufi_policy_routing.sh [ensure|check]" ;;
esac
