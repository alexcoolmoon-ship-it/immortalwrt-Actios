#!/bin/sh
# Validate before touching SIM GPIOs or resetting the radio.
set -eu

usage() {
    echo "Usage: $0 {1|2|3|4}" >&2
    exit 2
}

[ "$#" -eq 1 ] || usage
case "$1" in
    1) sel=1; en=0; sel2=0; en2=0 ;;
    2) sel=0; en=1; sel2=0; en2=0 ;;
    3) sel=0; en=0; sel2=1; en2=0 ;;
    4) sel=0; en=0; sel2=0; en2=1 ;;
    *) usage ;;
esac

for pin in sel en sel2 en2; do
    path="/sys/class/leds/sim:$pin/brightness"
    [ -w "$path" ] || { echo "SIM GPIO unavailable: $path" >&2; exit 1; }
done
for daemon in rmtfs modemmanager; do
    [ -x "/etc/init.d/$daemon" ] || { echo "Missing init script: $daemon" >&2; exit 1; }
done

printf '%s\n' "$sel" > /sys/class/leds/sim:sel/brightness
printf '%s\n' "$en" > /sys/class/leds/sim:en/brightness
printf '%s\n' "$sel2" > /sys/class/leds/sim:sel2/brightness
printf '%s\n' "$en2" > /sys/class/leds/sim:en2/brightness

rmmod qcom-q6v5-mss
modprobe qcom-q6v5-mss
/etc/init.d/rmtfs restart
/etc/init.d/modemmanager restart
