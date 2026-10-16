# Only use fresh, complete, unambiguous netifd IPv4 data. Never invent a gateway.
def ipv4:
  type == "string" and
  test("^([0-9]{1,3}\\.){3}[0-9]{1,3}$") and
  (split(".") | all(tonumber >= 0 and tonumber <= 255));
select(.up == true and .pending == false and .available == true and
       .autostart == true and .proto == "modemmanager" and (.uptime // 0) >= 20) |
select(.l3_device | type == "string" and test("^wwan[0-9]+$")) |
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
