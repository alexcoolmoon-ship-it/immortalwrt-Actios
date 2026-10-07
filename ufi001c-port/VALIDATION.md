# Validation boundary

OpenWrt f0a60eee2fe051741c643ea6118718aae1ef17fb (25.12.5).
Feeds: sources.lock.json; applications: addons.lock.json.

Local validation: shell/Python syntax; required-package selection with make
defconfig and check_config.py; no recursive Kconfig dependencies; checksums
of radio archive and every radio file; ARM64 identity of official zapret
binaries; synthetic evdev records; migration sparse/boot/layout rejection tests.

No full v2 compilation, physical GPIO test, EDL write, DoH or bypass test was
performed. The user's confirmed LTE success used exactly the included radio.
The updater only supports the known OpenStick layout; stock GPT/bootloader
conversion is refused before writes. Backup is read-only.
