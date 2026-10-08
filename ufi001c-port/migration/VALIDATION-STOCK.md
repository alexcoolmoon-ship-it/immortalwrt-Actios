# UFI001C stock installer 3.0-rc1 — validation boundaries

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
