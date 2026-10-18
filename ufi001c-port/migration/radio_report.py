"""Small public diagnostic, with no NV/IMEI/MAC or paths from the user's PC.

A whole-partition hash mismatch does NOT prove a different physical board:
FAT metadata or unused bytes can differ too. The report is not authorization
to flash an unknown radio; the installer's existing exact-match gate stays.
"""
import hashlib
import re


def summarize(data, profile):
    if not 512 <= len(data) <= 256 * 1024**2:
        raise ValueError('Unexpected factory radio partition length')
    digest = hashlib.sha256(data).hexdigest()
    expected = profile['stock_modem_sha256']
    # Export only narrowly recognized firmware build labels, never all strings.
    versions = sorted({m.decode('ascii') for m in re.findall(
        rb'\bUFI[0-9A-Z_-]{1,24}[ \t_]+20[0-9]{6}\b', data)})[:16]
    return {'format': 1, 'profile': profile['id'],
            'radio_bytes': len(data), 'stock_modem_sha256': digest,
            'expected_stock_modem_sha256': expected,
            'stock_modem_hash_matches': digest == expected,
            'firmware_labels': versions, 'nv_exported': False,
            'compatibility': 'known-radio-hash' if digest == expected else 'needs-review',
            'meaning': 'A partition hash mismatch alone does not identify a board revision. '
                       'A matching label alone does not prove compatible RF hardware.',
            'hardware_boot_test': 'not performed'}
