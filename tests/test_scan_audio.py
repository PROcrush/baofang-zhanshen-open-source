import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.scan_audio import scan_audio


class AudioFolderScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_recursive_supported_files_only_and_no_audio_read_or_network(self):
        (self.root / 'set').mkdir()
        for name in ('a.MP3', 'set/b.wav', 'set/c.flac', 'notes.txt'):
            (self.root / name).write_bytes(b'fixture metadata only')
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('network')), \
             patch.object(Path, 'open', side_effect=AssertionError('audio opened')):
            result = scan_audio(self.root)
        self.assertEqual({Path(path).name for path in result['paths']}, {'a.MP3', 'b.wav', 'c.flac'})
        self.assertFalse(result['truncated'])

    def test_links_are_not_followed_or_counted_as_real_audio(self):
        (self.root / 'real.wav').write_bytes(b'fixture')
        (self.root / 'link.mp3').symlink_to(self.root / 'real.wav')
        (self.root / 'cycle').symlink_to(self.root, target_is_directory=True)
        result = scan_audio(self.root)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['skippedLinks'], 2)

    def test_limit_is_explicit_and_truncated_set_cannot_claim_complete(self):
        for i in range(3):
            (self.root / f'{i}.wav').write_bytes(b'fixture')
        result = scan_audio(self.root, limit=2)
        self.assertEqual(result['count'], 2)
        self.assertTrue(result['truncated'])

    def test_empty_folder_and_invalid_input(self):
        self.assertEqual(scan_audio(self.root)['count'], 0)
        with self.assertRaises(ValueError):
            scan_audio(self.root, limit=True)
        file = self.root / 'file.wav'
        file.write_bytes(b'fixture')
        with self.assertRaises(ValueError):
            scan_audio(file)


if __name__ == '__main__':
    unittest.main()
