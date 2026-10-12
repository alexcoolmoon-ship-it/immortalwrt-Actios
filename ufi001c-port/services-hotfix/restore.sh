#!/bin/sh
set -eu
umask 077
cd "$(dirname "$0")"
[ -f gc.config ] && [ -f FILES ] && [ -d files ] || { echo 'Incomplete patch backup' >&2; exit 1; }
while read -r allowed new mode path; do
    if [ -f "files/$path" ]; then cp -p "files/$path" "/$path"; else rm -f "/$path"; fi
    rm -f "/$path.ufi-new"
done < FILES
cp -p gc.config /etc/config/gc
uci -q revert gc || true
if [ "$(cat enabled)" = 1 ]; then
    /etc/init.d/hotplug-gcreboot enable
    /etc/init.d/hotplug-gcreboot start
else
    /etc/init.d/hotplug-gcreboot stop
    /etc/init.d/hotplug-gcreboot disable
fi
echo 'Saved files and USB settings restored. Reload LuCI and restart Podkop if needed.'
