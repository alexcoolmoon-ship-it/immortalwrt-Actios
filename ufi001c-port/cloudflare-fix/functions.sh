# UFI001C_CLOUDFLARE_PATCH_V1. No daemon, timer, network/Wi-Fi reload or cache deletion.
cloudflare_first_proxy() {
    local kind
    [ -z "$cloudflare_selected" ] || return 0
    config_get kind "$1" connection_type
    [ "$kind" != proxy ] || cloudflare_selected="$1"
}

cloudflare_proxy_section() {
    local cloudflare_selected kind
    config_get cloudflare_selected settings cloudflare_dns_section
    if [ -z "$cloudflare_selected" ]; then
        config_get kind dns_only connection_type
        if [ "$kind" = proxy ] && section_configured dns_only; then
            cloudflare_selected=dns_only
        else
            foreach_configured_section cloudflare_first_proxy
        fi
    fi
    case "$cloudflare_selected" in ''|*[!A-Za-z0-9_]*) return 1;; esac
    config_get kind "$cloudflare_selected" connection_type
    [ "$kind" = proxy ] && section_configured "$cloudflare_selected" || return 1
    printf '%s\n' "$cloudflare_selected"
}

sing_box_cloudflare_patch() {
    local enabled section tag
    config_get_bool enabled settings cloudflare_dns_patch 0
    [ "$enabled" = 1 ] || return 0
    section=$(cloudflare_proxy_section) || { log 'Cloudflare requires a configured proxy section' error; return 1; }
    tag=$(get_outbound_tag_by_section "$section")
    config=$(printf '%s\n' "$config" | jq -e --arg tag "$tag" '
      def proxy_leaves($cfg; $name; $seen):
        if ($seen | index($name)) != null or ($seen | length) >= 16 then
          error("Cyclic Cloudflare proxy group")
        else [$cfg.outbounds[]? | select(.tag == $name)] as $hits |
          if ($hits|length) != 1 then error("Missing Cloudflare proxy outbound")
          else $hits[0] as $o |
            if ($o.detour // "") != "" then error("Chained Cloudflare proxy is unsupported")
            elif $o.type == "selector" or $o.type == "urltest" then
              (($o.outbounds // []) + ($o.fallbacks // [])) as $children |
              if ($children|length) == 0 then error("Empty Cloudflare proxy group")
              elif ($o.type == "selector" and ($o.default // "") != "" and
                    ($children|index($o.default)) == null) then error("Invalid proxy default")
              else [$children[] | proxy_leaves($cfg; .; $seen + [$name])[]] | unique end
            elif (["vless","trojan","shadowsocks","vmess","socks","http","hysteria2","tuic","anytls"]|index($o.type)) != null and
                 (($o.server // "")|length) > 0 then [$name]
            else error("Cloudflare DNS requires proxies only") end
          end
        end;
      . as $cfg | proxy_leaves($cfg; $tag; []) as $leaves |
      if ([.dns.servers[]? | select(.tag == "dns-server")]|length) != 1 or
         ([.dns.servers[]? | select(.tag == "bootstrap-dns-server")]|length) != 1 then
        error("Unexpected DNS configuration")
      else
        (.dns.servers[] | select(.tag == "dns-server")) = {
          type:"https", tag:"dns-server", server:"1.1.1.1", server_port:443,
          path:"/dns-query", detour:$tag,
          tls:{enabled:true, server_name:"cloudflare-dns.com"}} |
        (.dns.servers[] | select(.tag == "bootstrap-dns-server")) = {
          type:"tcp", tag:"bootstrap-dns-server", server:"77.88.8.8", server_port:53} |
        (.outbounds[] | select((.server // "") != "")).domain_resolver =
          {server:"bootstrap-dns-server", strategy:"ipv4_only"} |
        .route.rule_set = ((.route.rule_set // []) | map(if .type == "remote" then .download_detour = $tag else . end)) |
        .inbounds = ([.inbounds[]? | select(.tag != "service-mixed-in")] + [{
          type:"mixed",tag:"service-mixed-in",listen:"127.0.0.1",listen_port:4534}]) |
        .route.rules = ([{inbound:["service-mixed-in"],action:"route",outbound:$tag}] +
          [.route.rules[]? | select(((.inbound // [])|index("service-mixed-in")) == null)])
      end') || { log 'Cannot configure Cloudflare tunnel' error; return 1; }
}

cloudflare_doh_query_ok() (
    # Probe the same outbound and endpoint as the real resolver, not the cache
    # and not the operator-blocked direct DoH path.
    local conf work
    config_get conf settings config_path /etc/sing-box/config.json
    jq -e '
      . as $c | [.dns.servers[]? | select(.tag == "dns-server")] as $d |
      ($d|length) == 1 and $d[0].type == "https" and $d[0].server == "1.1.1.1" and
      $d[0].path == "/dns-query" and ($d[0].detour // "") != "" and
      any(.inbounds[]?; .tag == "service-mixed-in" and .listen == "127.0.0.1" and .listen_port == 4534) and
      any(.route.rules[]?; .outbound == $d[0].detour and
          ((.inbound // [])|index("service-mixed-in")) != null)
    ' "$conf" >/dev/null || return 1
    work=$(mktemp -d /tmp/ufi-cf-probe.XXXXXX) || return 1
    trap 'rm -rf "$work"' EXIT HUP INT TERM
    printf '\000\000\001\000\000\001\000\000\000\000\000\000\007example\003com\000\000\001\000\001' > "$work/query"
    curl --noproxy '' --proxy http://127.0.0.1:4534 --fail --silent --show-error \
      --connect-timeout 4 --max-time 12 \
      -H 'Content-Type: application/dns-message' -H 'Accept: application/dns-message' \
      --data-binary "@$work/query" https://1.1.1.1/dns-query > "$work/answer" 2>/dev/null || return 1
    set -- $(hexdump -v -n 12 -e '1/1 "%u "' "$work/answer")
    [ "$#" = 12 ] || return 1
    [ "$1" = 0 ] && [ "$2" = 0 ] && [ "$(($3 & 128))" != 0 ] &&
      [ "$(($3 & 2))" = 0 ] && [ "$(($4 & 15))" = 0 ] &&
      [ "$(($5 * 256 + $6))" = 1 ] && [ "$(($7 * 256 + $8))" -gt 0 ] &&
      [ "$(wc -c < "$work/answer")" -gt 33 ]
)

cloudflare_generate() {
    local cloudflare_output="$1"
    mkdir -p "$TMP_RULESET_FOLDER" || return 1
    sing_box_save_config() {
        sing_box_cm_save_config_to_file "$config" "$cloudflare_output" || return 1
        sing-box check -c "$cloudflare_output" >/dev/null 2>&1
    }
    sing_box_init_config
}
