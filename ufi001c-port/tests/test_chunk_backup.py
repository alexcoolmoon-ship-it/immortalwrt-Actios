"""Resumable read tests use a tiny synthetic disk, never physical hardware."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'migration'))
import chunk_backup as b


class ChunkBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b''.join(bytes([i])*512 for i in range(9))
        self.profile = {'id':'fixture', 'disk_bytes':len(self.data)}
        self.identity = {'regions':[{'name':'unit','offset':512,'bytes':512,
                                    'sha256':hashlib.sha256(self.data[512:1024]).hexdigest()}]}
        self.fail_at = None; self.short = False; self.calls = []

    def edl(self, action, start, sectors, out):
        self.assertEqual(action, 'rs', 'backup must NEVER write device')
        self.calls.append((start, sectors))
        if start == self.fail_at:
            Path(out).write_bytes(b'partial USB read')
            if self.short: return
            raise OSError('simulated USBError(5)')
        Path(out).write_bytes(self.data[start*512:(start+sectors)*512])

    def read(self, identity=None):
        return b.full_backup(self.edl, self.root, self.profile,
                             identity or (lambda: copy.deepcopy(self.identity)), chunk_bytes=1024)

    def test_every_sector_including_tail_is_preserved_and_hashed(self):
        result = self.read()
        self.assertEqual(result.read_bytes(), self.data)
        self.assertEqual(self.calls, [(0,2),(2,2),(4,2),(6,2),(8,1)])
        state = json.loads((self.root/'backup-state.json').read_text())
        self.assertEqual(state['status'], 'read-complete')
        self.assertEqual(len(state['chunks']), 5)
        self.assertFalse((self.root/'emmc.partial').exists())

    def test_usb_failure_then_resume_does_not_reread_completed_chunks(self):
        self.fail_at = 4
        with self.assertRaises(OSError): self.read()
        self.assertFalse((self.root/'emmc.bin').exists())
        self.assertEqual((self.root/'emmc.partial').stat().st_size, 2048)
        state = json.loads((self.root/'backup-state.json').read_text())
        self.assertEqual(state['next_sector'], 4)
        self.fail_at = None; self.calls.clear()
        self.assertEqual(self.read().read_bytes(), self.data)
        self.assertEqual(self.calls, [(4,2),(6,2),(8,1)])

    def test_short_successful_edl_result_is_never_checkpointed(self):
        self.fail_at = 2; self.short = True
        with self.assertRaisesRegex(ValueError, 'Incomplete EDL chunk'): self.read()
        self.assertEqual(len(json.loads((self.root/'backup-state.json').read_text())['chunks']), 1)
        self.assertFalse((self.root/'emmc.bin').exists())

    def test_other_modem_resume_is_rejected_before_next_chunk(self):
        self.fail_at = 4
        with self.assertRaises(OSError): self.read()
        other = copy.deepcopy(self.identity); other['regions'][0]['sha256'] = '0'*64
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'identity/profile mismatch'):
            self.read(lambda:other)
        self.assertEqual(self.calls, [])

    def test_modified_saved_data_is_rejected(self):
        self.fail_at = 4
        with self.assertRaises(OSError): self.read()
        with (self.root/'emmc.partial').open('r+b') as f: f.write(b'X')
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'): self.read()
        self.assertEqual(self.calls, [])

    def test_uncheckpointed_host_tail_after_crash_is_discarded_and_reread(self):
        self.fail_at = 4
        with self.assertRaises(OSError): self.read()
        with (self.root/'emmc.partial').open('ab') as f: f.write(b'uncommitted host tail')
        self.fail_at = None; self.calls.clear()
        self.assertEqual(self.read().read_bytes(), self.data)
        self.assertEqual(self.calls[0], (4,2))

    def test_completed_backup_is_rechecked_without_new_large_read(self):
        self.read(); self.calls.clear()
        self.assertEqual(self.read().read_bytes(), self.data)
        self.assertEqual(self.calls, [])
        with (self.root/'emmc.bin').open('ab') as f: f.write(b'corrupt tail')
        with self.assertRaisesRegex(ValueError, 'length/checkpoints'): self.read()

    def test_device_changed_during_read_is_not_a_complete_backup(self):
        count = [0]
        def identity():
            count[0] += 1
            obj = copy.deepcopy(self.identity)
            if count[0] > 1: obj['regions'][0]['sha256'] = 'f'*64
            return obj
        with self.assertRaisesRegex(ValueError, 'changed during'): self.read(identity)
        self.assertFalse((self.root/'emmc.bin').exists())

    def test_corrupt_or_skipped_checkpoints_cannot_skip_sectors(self):
        self.fail_at = 4
        with self.assertRaises(OSError): self.read()
        path = self.root/'backup-state.json'; state=json.loads(path.read_text())
        state['chunks'][1]['offset'] += 512; path.write_text(json.dumps(state))
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'Non-contiguous'): self.read()
        self.assertEqual(self.calls, [])

    def test_unknown_legacy_partial_dump_is_not_reused(self):
        (self.root/'emmc.bin').write_bytes(self.data[:2048])
        with self.assertRaisesRegex(ValueError, 'without checkpoints'): self.read()
        self.assertEqual(self.calls, [])

    def test_readback_is_chunked_and_stops_on_first_error(self):
        target=self.root/'verify.bin'
        b.read_range(self.edl,0,9,target,1024)
        self.assertEqual(target.read_bytes(),self.data)
        self.calls.clear(); self.fail_at=2
        with self.assertRaises(OSError): b.read_range(self.edl,0,9,target,1024)
        self.assertEqual(self.calls,[(0,2),(2,2)])

    def test_one_backup_folder_cannot_have_concurrent_writers(self):
        with b.exclusive(self.root):
            with self.assertRaises(OSError): self.read()
        self.assertEqual(self.calls, [])


if __name__ == '__main__': unittest.main()
