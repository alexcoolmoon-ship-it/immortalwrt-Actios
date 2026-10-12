def outbound($doc; $tag): [$doc.outbounds[]? | select(.tag == $tag)];
def proxy_tree($doc; $tag; $seen):
  if ($tag|type) != "string" or ($seen|index($tag)) != null or ($seen|length) >= 16 then false
  else outbound($doc; $tag) as $o |
    if ($o|length) != 1 then false
    else $o[0] as $o |
      if $o.detour? != null then false
      elif $o.type == "selector" or $o.type == "urltest" then
        if ($o.outbounds|type) != "array" or (($o.fallbacks // [])|type) != "array" then false else
        ($o.outbounds + ($o.fallbacks // [])) as $members |
        ($members|length) > 0 and
        all($members[]; proxy_tree($doc; .; $seen + [$tag])) and
        (if $o.default? != null then ($members|index($o.default)) != null else true end) end
      else (["vless","vmess","trojan","shadowsocks","socks","http","hysteria","hysteria2","tuic","anytls","shadowtls","ssh"]|index($o.type)) != null and
           (($o.server // "")|type) == "string" and (($o.server // "")|length) > 0
      end
    end
  end;
. as $doc |
[.inbounds[]? | select(.tag == "service-mixed-in")] as $ins |
[.route.rules[]? | select((.inbound // [] | if type == "string" then [.] else . end | index("service-mixed-in")) != null)] as $rules |
($ins|length) == 1 and $ins[0].type == "mixed" and $ins[0].listen == "127.0.0.1" and $ins[0].listen_port == 4534 and
($rules|length) == 1 and proxy_tree($doc; $rules[0].outbound; [])
