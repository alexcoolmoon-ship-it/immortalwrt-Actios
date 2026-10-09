#!/bin/sh
# UFI001C v3.1.2: section names are user choices, not firmware dependencies.
# Sourced by Podkop after /lib/functions.sh; no writes to UCI or the network.

ufi_section_ready() {
    local section="$1" kind format payload
    config_get kind "$section" connection_type
    case "$kind" in
        proxy)
            config_get format "$section" proxy_config_type url
            case "$format" in
                url) config_get payload "$section" proxy_string ;;
                outbound) config_get payload "$section" outbound_json ;;
                selector) config_get payload "$section" selector_proxy_links ;;
                urltest) config_get payload "$section" urltest_proxy_links ;;
                *) return 0 ;; # Keep malformed configurations visible to validation.
            esac ;;
        vpn) config_get payload "$section" interface ;;
        block|exclusion) return 0 ;;
        '') return 1 ;; # Newly added, still empty form.
        *) return 0 ;;
    esac
    printf '%s' "$payload" | grep -q '[^[:space:]]'
}

_ufi_dispatch_section() {
    local section="$1" callback="$2"
    shift 2
    ufi_section_ready "$section" || return 0
    "$callback" "$section" "$@"
}

ufi_each_section() {
    local callback="$1"
    shift
    config_foreach _ufi_dispatch_section section "$callback" "$@"
}

ufi_dns_candidate() {
    local section="$1" kind format
    [ -n "$section" ] && [ "$section" != '@auto' ] || return 1
    config_get kind "$section" connection_type
    config_get format "$section" proxy_config_type url
    [ "$kind" = proxy ] || return 1
    case "$format" in url|outbound) ;; *) return 1 ;; esac
    ufi_section_ready "$section"
}

_ufi_first_dns_candidate() {
    [ -z "$ufi_first" ] || return 0
    ufi_dns_candidate "$1" && ufi_first="$1"
    return 0
}

ufi_resolve_dns_section() {
    local requested="${1:-}" download ufi_first=''
    [ -n "$requested" ] || config_get requested settings ufi_dns_proxy_section '@auto'
    if ufi_dns_candidate "$requested"; then
        printf '%s\n' "$requested"
        return 0
    fi
    config_get download settings download_lists_via_proxy_section
    if ufi_dns_candidate "$download"; then
        printf '%s\n' "$download"
        return 0
    fi
    config_foreach _ufi_first_dns_candidate section
    [ -n "$ufi_first" ] || return 1
    printf '%s\n' "$ufi_first"
}

ufi_prepare_runtime() {
    local requested selected download
    config_get requested settings ufi_dns_proxy_section
    if [ -n "$requested" ]; then
        selected=$(ufi_resolve_dns_section "$requested") || {
            log 'DNS via proxy needs a filled single-URL or single-outbound proxy section. Empty templates are ignored.' fatal
            return 1
        }
        # Runtime config only. Preserve the automatic/user choice in /etc/config.
        config_set settings ufi_dns_proxy_section "$selected"
        config_set settings download_lists_via_proxy 1
        config_set settings download_lists_via_proxy_section "$selected"
        log "DNS and list downloads use proxy section '$selected'" info
    else
        config_get download settings download_lists_via_proxy_section
        if [ "$download" = '@auto' ] || ! ufi_section_ready "$download"; then
            selected=$(get_first_outbound_section)
            [ -z "$selected" ] || config_set settings download_lists_via_proxy_section "$selected"
        fi
    fi
}

case "$0" in
    */ufi_startup.sh|ufi_startup.sh)
        . /lib/functions.sh
        config_load podkop
        case "${1:-resolve}" in
            resolve) ufi_resolve_dns_section "${2:-}" ;;
            *) echo 'Usage: sh ufi_startup.sh resolve [section|@auto]' >&2; exit 1 ;;
        esac
        ;;
esac
