# UFI001C stock installer 3.1.4-rc2 — validation boundaries

This is a first-stock-hardware-test candidate, not a claim that the stock
conversion has already booted on a second unit. The user confirmed that v2
boots after using the existing-layout installer 2.1.0.

The offline suite checks both GPT copies and CRCs, exact rootfs partition
number and disk end, per-device NV transfer, untouched recovery identity
regions, GPT commit order, refusal of different stock radio/layout, modified
image refusal, stopping on a corrupt readback, mismatched-device refusal,
archive path safety and full-restore orchestration. The existing migration
suite also checks Android sparse images, sector alignment and real OpenWrt
fwtool metadata. These tests use synthetic disks/firmware; no physical USB
device is accessed by the tests.

Backup validation means GPT CRC/copy consistency, exact expected disk length,
known factory profile/radio SHA256, and a locally computed full-backup SHA256.
It is not a claim that every backup byte was read twice from the device.
Before writing, current GPT and original modemst1/modemst2 are compared to
the backup. Each written range is read back, its exact length and SHA256
compared. The two original modemst areas are outside all conversion writes.
Restoration is bound to these original areas and compares a full readback.

Writes include boot, rootfs, own NV and sec, blank devinfo, OpenStick base
bootchain, then secondary GPT and primary GPT last. The bootchain update is
not atomic: interruption may require EDL restoration. EDL remains the recovery
method and has been usable on the user's existing unit, but availability on
a different hardware revision is not guaranteed by a chipset name alone.

The bundled bootchain files match OpenStick v1 base.zip byte for byte. The
source partition order is retained; size placeholders are replaced with the
exact eMMC bounds and fresh GPT GUIDs/CRCs. This does not bypass secure-boot
fuses or establish support for hardware with a different trust configuration.

Stock scope is intentionally narrow: the factory GPT geometry and immutable
modem partition hash recovered from the original UFI001C factory backup.
No factory NV values from that device are bundled. The user must check the
physical UFI001C board marking before confirming the write.

Still needed on hardware: the first complete RC1 stock conversion, Windows
launcher/file picker/driver interaction, LTE and Wi-Fi, reset button behavior,
and a real restoration trial. Kernel compilation is not repeated for a change
to the host installer.

October 11 revision: exact 4GB profile (3875536896 bytes, userdata last LBA 7569374)
added alongside the original 8GB profile. Every other partition matches the
observed stock GPT. The same factory-radio SHA256 gate remains mandatory.
New tests cover exact profile selection, unknown/moved layouts, 4GB write
bounds, secondary GPT placement and probe read-only scope. The read-only probe
never reads NV, never writes the device, and is not a full backup.
The full installer still validates actual full-dump size and both GPT copies.

October 15 revision: supervise the existing EDL subprocess, without changing
its loader, USB driver, command arguments or write plan. Read commands abort
on a reported USBError/USB Overflow/LIBUSB_ERROR, or after 180 seconds with
no increase in the output file size. Output is drained through a bounded
4-MiB-per-command log; failures also produce a small edl-failure.json.
Writes keep the previous total deadline and never receive the read-idle rule;
no automatic reconnect, reset or retry is introduced. USB errors at a zero
process exit are still errors, so validation cannot advance on false success.

Real fake-EDL subprocess tests cover a stalled partial file, no initial data,
progress resetting the deadline, a USB error flood, split error text, zero
exit with USB errors, errors after the log cap, large-output pipe draining,
write timeout separation, nonzero exits and stopping the install flow before
backup verification/confirmation/writes on read failure. These are host tests,
not a physical Windows USB/driver or eMMC validation. A successful full backup
on the user's new modem remains required before conversion.
