"""Pinned binary input downloads must fail closed before freezing Windows EDL."""
import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PORT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ufi_windows_builder', PORT/'ci/build_windows.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class WindowsSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = b'locked Firehose input'
        self.name = 'vendor/007050e100000000_test.bin'
        self.lock = {'repository':'https://github.com/bkerler/Loaders', 'commit':'a'*40,
                     'files':{self.name:{'bytes':len(self.payload),
                              'sha256':hashlib.sha256(self.payload).hexdigest()}}}

    def test_exact_locked_commit_file_and_size_are_used(self):
        stream=io.BytesIO(self.payload)
        with patch.object(builder,'urlopen',return_value=stream) as fetch:
            builder.export_loaders(self.lock,self.root)
        fetch.assert_called_once_with('https://raw.githubusercontent.com/bkerler/Loaders/'+'a'*40+'/'+self.name,
                                      timeout=30)
        self.assertEqual((self.root/self.name).read_bytes(),self.payload)
        self.assertEqual(len(list(self.root.rglob('*.bin'))),1)

    def test_wrong_truncated_or_oversized_bytes_are_never_written(self):
        for data in (b'x'*len(self.payload),self.payload[:-1],self.payload+b'extra'):
            with self.subTest(data=data), patch.object(builder,'urlopen',return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError,'checksum'):
                    builder.export_loaders(self.lock,self.root)
            self.assertFalse((self.root/self.name).exists())

    def test_network_failure_is_terminal(self):
        with patch.object(builder,'urlopen',side_effect=OSError('connection lost')):
            with self.assertRaises(OSError): builder.export_loaders(self.lock,self.root)
        self.assertFalse((self.root/self.name).exists())

    def test_unpinned_repository_revision_and_unsafe_path_are_rejected_before_network(self):
        for key,value in [('repository','https://example.com/Loaders'),('commit','main'),
                          ('commit','z'*40),('files',{'../outside.bin':self.lock['files'][self.name]})]:
            lock=copy.deepcopy(self.lock);lock[key]=value
            with patch.object(builder,'urlopen') as fetch:
                with self.assertRaises(ValueError): builder.export_loaders(lock,self.root)
                fetch.assert_not_called()


if __name__=='__main__': unittest.main()
