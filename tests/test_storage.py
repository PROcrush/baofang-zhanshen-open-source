"""Version binding and external lyric validation with actual temporary WAVs."""
import copy
import hashlib
import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from app.storage import Library, inspect_audio, validate_timeline


def make_wav(path, amplitude=1200):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(struct.pack('<h', amplitude) * 32000)


def timeline(version='sha256:test', quality='word'):
    return {
        'schemaVersion': 1,
        'versionId': version,
        'durationMs': 4000.0,
        'quality': quality,
        'segments': [
            {'startMs': 500.0, 'endMs': 1500.0, 'text': '甲乙', 'words': [
                {'startMs': 500.0, 'endMs': 900.0, 'text': '甲'},
                {'startMs': 1100.0, 'endMs': 1500.0, 'text': '乙'},
            ]},
            {'startMs': 2500.0, 'endMs': 3500.0, 'text': '丙', 'words': [
                {'startMs': 2500.0, 'endMs': 3500.0, 'text': '丙'},
            ]},
        ],
    }


class AudioIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'same name.wav'
        make_wav(self.source)
        self.library = Library(self.root / 'library')

    def tearDown(self):
        self.library.close()
        self.temporary.cleanup()

    def test_real_wav_identity_matches_bytes_and_duration(self):
        track = inspect_audio(self.source)
        expected_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(track['versionId'], 'sha256:' + expected_hash)
        self.assertEqual(track['durationMs'], 4000.0)
        self.assertEqual(track['path'], str(self.source.resolve()))
        self.assertFalse(track['isDemo'])
        self.assertEqual(track['quality'], 'unavailable')

    def test_identical_copies_deduplicate_versions_and_keep_path_bindings(self):
        duplicate = self.root / 'another name.wav'
        shutil.copyfile(self.source, duplicate)
        first, second = inspect_audio(self.source), inspect_audio(duplicate)
        self.library.add_track(first)
        self.library.add_track(second)
        self.assertEqual(first['versionId'], second['versionId'])
        self.assertEqual(len(self.library.tracks()), 1)
        bindings = self.library.connection.execute(
            'SELECT path, version_id FROM bindings').fetchall()
        self.assertEqual(set(bindings), {(str(self.source.resolve()), first['versionId']),
                                         (str(duplicate.resolve()), first['versionId'])})

    def test_same_filename_and_duration_different_bytes_have_distinct_versions(self):
        first = inspect_audio(self.source)
        self.library.add_track(first)
        make_wav(self.source, amplitude=2400)
        second = inspect_audio(self.source)
        self.library.add_track(second)
        self.assertNotEqual(first['versionId'], second['versionId'])
        self.assertEqual(first['durationMs'], second['durationMs'])
        self.assertEqual(len(self.library.tracks()), 2)
        binding = self.library.connection.execute(
            'SELECT version_id FROM bindings WHERE path=?', (str(self.source.resolve()),)).fetchone()
        self.assertEqual(binding[0], second['versionId'])

    def test_replaced_file_cannot_receive_old_version_timeline(self):
        first = inspect_audio(self.source)
        self.library.add_track(first)
        make_wav(self.source, amplitude=2400)
        with self.assertRaisesRegex(ValueError, '替换'):
            self.library.add_timeline(timeline(first['versionId']))
        self.assertIsNone(self.library.timeline(first['versionId']))
        self.assertEqual(self.library.get_track(first['versionId'])['quality'], 'unavailable')

    def test_valid_timeline_persists_and_reindex_does_not_downgrade_quality(self):
        track = inspect_audio(self.source)
        self.library.add_track(track)
        payload = timeline(track['versionId'])
        self.library.add_timeline(payload)
        self.library.add_track(inspect_audio(self.source))
        self.assertEqual(self.library.get_track(track['versionId'])['quality'], 'word')
        self.library.close()
        self.library = Library(self.root / 'library')
        self.assertEqual(self.library.timeline(track['versionId']), payload)
        self.assertIn('尚未声学校验', self.library.get_track(track['versionId'])['status'])

    def test_mismatched_duration_does_not_commit_a_binding(self):
        track = inspect_audio(self.source)
        self.library.add_track(track)
        payload = timeline(track['versionId'])
        payload['durationMs'] = 5000
        with self.assertRaises(ValueError):
            self.library.add_timeline(payload)
        self.assertIsNone(self.library.timeline(track['versionId']))

    def test_missing_version_must_be_indexed_first(self):
        with self.assertRaisesRegex(ValueError, '先导入'):
            self.library.add_timeline(timeline())

    def test_non_audio_file_and_directory_refused(self):
        unsupported = self.root / 'text.txt'
        unsupported.write_text('not an audio file')
        for source in (unsupported, self.root):
            with self.subTest(source=source), self.assertRaises(ValueError):
                inspect_audio(source)

    def test_job_state_and_payload_persist_without_audio_content(self):
        self.library.record_job('test-job', 'failed', {'error': 'missing source'})
        row = self.library.connection.execute(
            'SELECT state, payload FROM jobs WHERE job_id=?', ('test-job',)).fetchone()
        self.assertEqual(row[0], 'failed')
        self.assertEqual(json.loads(row[1]), {'error': 'missing source'})


class TimelineValidationTests(unittest.TestCase):
    def test_sparse_sentence_and_word_timelines_are_valid(self):
        for quality in ('sentence', 'word'):
            payload = timeline(quality=quality)
            with self.subTest(quality=quality):
                self.assertEqual(validate_timeline(payload, payload['versionId'], 4000), payload)

    def test_version_and_protocol_must_match(self):
        payload = timeline()
        with self.assertRaises(ValueError):
            validate_timeline(payload, 'sha256:other')
        payload['schemaVersion'] = 2
        with self.assertRaises(ValueError):
            validate_timeline(payload, 'sha256:test')

    def test_nonfinite_negative_boolean_and_string_times_refused(self):
        for invalid in (float('nan'), float('inf'), -float('inf'), -1, True, '500'):
            for where in ('duration', 'segment', 'word'):
                payload = timeline()
                if where == 'duration':
                    payload['durationMs'] = invalid
                elif where == 'segment':
                    payload['segments'][0]['startMs'] = invalid
                else:
                    payload['segments'][0]['words'][0]['endMs'] = invalid
                with self.subTest(invalid=invalid, where=where), self.assertRaises(ValueError):
                    validate_timeline(payload, 'sha256:test')

    def test_reversed_overlap_out_of_order_and_overrun_segments_refused(self):
        cases = []
        payload = timeline()
        payload['segments'][0]['endMs'] = 400
        cases.append(payload)
        payload = timeline()
        payload['segments'][1]['startMs'] = 1400
        cases.append(payload)
        payload = timeline()
        payload['segments'].reverse()
        cases.append(payload)
        payload = timeline()
        payload['segments'][1]['endMs'] = 4001
        cases.append(payload)
        for payload in cases:
            with self.subTest(segments=payload['segments']), self.assertRaises(ValueError):
                validate_timeline(payload, 'sha256:test')

    def test_word_overlap_reversal_bounds_and_text_mismatch_refused(self):
        original = timeline()
        changes = [
            (0, 'startMs', 499),
            (0, 'endMs', 400),
            (1, 'startMs', 800),
            (1, 'endMs', 1501),
            (1, 'text', '错误'),
        ]
        for index, key, value in changes:
            payload = copy.deepcopy(original)
            payload['segments'][0]['words'][index][key] = value
            with self.subTest(index=index, key=key, value=value), self.assertRaises(ValueError):
                validate_timeline(payload, 'sha256:test')

    def test_empty_text_empty_segments_and_missing_words_refused(self):
        cases = []
        payload = timeline()
        payload['segments'][0]['text'] = '  '
        cases.append(payload)
        payload = timeline()
        payload['segments'] = []
        cases.append(payload)
        payload = timeline()
        del payload['segments'][0]['words']
        cases.append(payload)
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_timeline(payload, 'sha256:test')


if __name__ == '__main__':
    unittest.main()
