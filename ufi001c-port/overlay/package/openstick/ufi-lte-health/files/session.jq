# Only use fresh, complete, unambiguous netifd IPv4 data. Never invent a gateway.
# OpenWrt's small jq can lack Oniguruma. Do not use test/match/sub here.
def digits:
  if type != "string" then false
  else length > 0 and (explode | all(. >= 48 and . <= 57)) end;
def ipv4:
  if type != "string" then false else
    split(".") | length == 4 and
    all(.[]; if digits and length <= 3 then tonumber <= 255 else false end)
  end;
def wwan:
  if type != "string" then false
  elif startswith("wwan") then .[4:] | digits else false end;
select(.up == true and .pending == false and .available == true and
       .autostart == true and .proto == "modemmanager" and (.uptime // 0) >= 20) |
select(.l3_device | wwan) |
select(."ipv4-address" | length == 1) |
. as $s | ."ipv4-address"[0] as $a |
[.route[]? | select(.target == "0.0.0.0" and .mask == 0)] as $routes |
select($routes | length == 1) | $routes[0] as $r |
select(($a.address | ipv4) and ($r.nexthop | ipv4)) |
select(($a.mask | type == "number") and $a.mask >= 1 and $a.mask <= 32) |
select(($r.table // 254) == 254 or ($r.table // 254) == 0) |
select(($r.source // ($a.address + "/32")) == ($a.address + "/32")) |
select((.metric // 0) | type == "number" and . >= 0 and . <= 4294967295 and floor == .) |
{dev: .l3_device, address: $a.address, prefix: $a.mask, gateway: $r.nexthop,
 metric: (.metric // 0)}
