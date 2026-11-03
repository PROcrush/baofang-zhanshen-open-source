import plistlib
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from probes.rekordbox_probe import embedded_entitlements, inspect_rekordbox


class RekordboxProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name) / 'rekordbox.app'
        (self.app / 'Contents/MacOS').mkdir(parents=True)
        self.binary = self.app / 'Contents/MacOS/rekordbox'
        self.binary.write_bytes(b'test executable identity')
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.pioneerdj.rekordboxdj',
            'CFBundleExecutable': 'rekordbox', 'CFBundleShortVersionString': '7.2.19.0342'}))
        self.query = Mock(return_value=subprocess.CompletedProcess([], 0, '123\n', ''))
        self.paths = Mock(return_value=self.binary.resolve())
        self.port = Mock(return_value={'code': 5, 'detail': '(os/kern) failure', 'taskPortAcquired': False})

    def inspect(self):
        with patch('probes.rekordbox_probe.sys.platform', 'darwin'):
            return inspect_rekordbox(self.app, run=self.query, path_for_pid=self.paths, check_port=self.port)

    def test_permission_denial_preserves_unavailable_decks(self):
        report = self.inspect()
        self.assertEqual(report['checks'][0]['code'], 'task_port_denied')
        self.assertFalse(report['liveConnectionVerified'])
        self.assertEqual(report['memoryBytesRead'], 0)
        self.assertEqual([d['deck'] for d in report['decks']], ['A', 'B'])
        self.assertTrue(all(d['positionMs'] is None and d['playing'] is None for d in report['decks']))

    def test_task_port_success_does_not_claim_deck_connection(self):
        self.port.return_value = {'code': 0, 'detail': 'success', 'taskPortAcquired': True}
        report = self.inspect()
        self.assertEqual(report['checks'][0]['status'], 'ok')
        self.assertFalse(report['liveConnectionVerified'])
        self.assertEqual(report['checks'][-1]['code'], 'deck_mapping_not_live_validated')

    def test_process_query_error_is_not_reported_as_not_running(self):
        self.query.return_value = subprocess.CompletedProcess([], 1, '', 'Cannot get process list')
        self.assertEqual(self.inspect()['checks'][0]['code'], 'process_query_failed')
        self.port.assert_not_called()

    def test_pid_from_different_application_is_not_attached(self):
        self.paths.return_value = Path('/different/rekordbox')
        self.assertEqual(self.inspect()['checks'][-1]['code'], 'process_identity_not_unique')
        self.port.assert_not_called()

    def test_ambiguous_matching_processes_are_not_attached(self):
        self.query.return_value.stdout = '123\n456\n'
        self.assertEqual(self.inspect()['checks'][-1]['matches'], [123, 456])
        self.port.assert_not_called()

    def test_absent_process_keeps_both_decks_unavailable(self):
        self.query.return_value = subprocess.CompletedProcess([], 1, '', '')
        self.assertEqual(self.inspect()['checks'][0]['code'], 'not_running')
        self.port.assert_not_called()

    def test_other_bundle_is_rejected_before_process_access(self):
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'other'}))
        with self.assertRaises(ValueError):
            self.inspect()
        self.query.assert_not_called()

    def test_invalid_signature_cannot_be_used_to_replace_entitlements(self):
        self.binary.write_bytes(b'broken')
        with self.assertRaises((ValueError, struct.error)):
            embedded_entitlements(self.binary)

    def test_embedded_rights_are_preserved_for_both_architectures(self):
        rights = {'com.apple.security.device.audio-input': True,
                  'com.apple.security.cs.disable-library-validation': True}
        xml = plistlib.dumps(rights)
        blob = struct.pack('>II', 0xFADE7171, len(xml) + 8) + xml
        signature = struct.pack('>IIIII', 0xFADE0CC0, len(blob) + 20, 1, 5, 20) + blob
        slices = []
        for cpu in (0x1000007, 0x100000C):
            header = struct.pack('<IiiIIIII', 0xFEEDFACF, cpu, 0, 2, 1, 16, 0, 0)
            slices.append(header + struct.pack('<IIII', 0x1D, 16, 48, len(signature)) + signature)
        first_offset = 48
        second_offset = first_offset + len(slices[0])
        fat = struct.pack('>II', 0xCAFEBABE, 2)
        fat += struct.pack('>IIIII', 0x1000007, 0, first_offset, len(slices[0]), 0)
        fat += struct.pack('>IIIII', 0x100000C, 0, second_offset, len(slices[1]), 0)
        self.binary.write_bytes(fat + b''.join(slices))
        parsed = embedded_entitlements(self.binary)
        self.assertEqual([item['cpu'] for item in parsed], [0x1000007, 0x100000C])
        self.assertEqual([item['entitlements'] for item in parsed], [rights, rights])
        self.assertNotIn('com.apple.security.get-task-allow', parsed[0]['entitlements'])


if __name__ == '__main__':
    unittest.main()
