#!/usr/bin/env python3
"""Reject firmware without IPv4 policy routing, required by Podkop TProxy."""
import pathlib
import sys


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: check_kernel.py OPENWRT_BUILD_ROOT')
    root = pathlib.Path(sys.argv[1])
    configs = list(root.glob('build_dir/target-*/linux-*/linux-[0-9]*/.config'))
    if len(configs) != 1:
        raise SystemExit(f'Expected one built target kernel config, found {len(configs)}')
    values = set(configs[0].read_text().splitlines())
    required = ('CONFIG_IP_ADVANCED_ROUTER=y', 'CONFIG_IP_MULTIPLE_TABLES=y', 'CONFIG_FIB_RULES=y')
    missing = [item for item in required if item not in values]
    if missing:
        raise SystemExit('STOP: Podkop IPv4 policy routing unavailable: ' + ', '.join(missing))
    print('PASS: built kernel supports IPv4 policy routing for Podkop')


if __name__ == '__main__':
    main()
