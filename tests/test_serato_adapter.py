import copy
import tempfile
import time
import unittest
import wave
from pathlib import Path

from app.messages import accept_serato_frame, validate_program_payload, validate_serato_frame
from app.serato_adapter import (adapt_serato_frame, assign_serato_lyric_versions,
                                proof_is_current, serato_lyric_index)
from app.storage import inspect_audio
from app.styles import DEFAULT_STYLE, changed_style, validate_style
from app.clock import monotonic_ns


def frame():
    now = monotonic_ns()
    return {'type': 'serato_frame', 'mode': 'serato', 'protocolVersion': 1, 'epoch': 'live:1', 'sequence': 1,
            'capturedNs': now, 'validUntilNs': now + 350_000_000, 'producerVerified': True,
            'decks': [{'deck_index': i, 'source_identity': i + 1, 'audio_path': '',
                       'position_seconds': 1.25, 'velocity': 0.0} for i in (0, 1)],
            'fader': {'normalized': .5, 'left_deck_index': 0, 'right_deck_index': 1}}


class LiveMessageTests(unittest.TestCase):
    def test_valid_complete_frame_and_sequence(self):
        sample = frame()
        self.assertIs(validate_serato_frame(sample), sample)
        self.assertTrue(accept_serato_frame(None, 0, sample))
        self.assertFalse(accept_serato_frame('live:1', 1, sample))
        sample['sequence'] = 2
        self.assertTrue(accept_serato_frame('live:1', 1, sample))

    def test_incomplete_identity_route_or_nonfinite_rejected(self):
        for edit in (lambda v: v.update(producerVerified=False),
                     lambda v: v.update(decks=v['decks'][:1]),
                     lambda v: v['decks'][1].update(deck_index=0),
                     lambda v: v['decks'][1].update(source_identity=1),
                     lambda v: v['decks'][0].update(position_seconds=float('nan')),
                     lambda v: v['decks'][0].update(velocity=True),
                     lambda v: v['fader'].update(normalized=2),
                     lambda v: v['fader'].update(right_deck_index=0),
                     lambda v: v.update(validUntilNs=v['capturedNs']+350_000_001)):
            value = frame()
            edit(value)
            with self.assertRaises(ValueError):
                validate_serato_frame(value)

    def test_reverse_and_pause_remain_observations_not_commands(self):
        value = frame()
        value['decks'][1]['velocity'] = -1
        raw = adapt_serato_frame(value, {})
        self.assertFalse(raw['A']['playing'])
        self.assertEqual(raw['B']['speed'], -1)
        self.assertEqual(raw['A']['durationMs'], 0)


class ActualFileIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.wav'
        self.write_wave(0)

    def tearDown(self):
        self.temp.cleanup()

    def write_wave(self, value):
        with wave.open(str(self.path), 'wb') as audio:
            audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            audio.writeframes(bytes([value, 0]) * 16000)

    def test_actual_hash_required_before_cached_timeline_binding(self):
        value = frame()
        value['decks'][0]['audio_path'] = str(self.path)
        pending = adapt_serato_frame(value, {})['A']
        self.assertFalse(pending['identityReady'])
        self.assertTrue(pending['versionId'].startswith('pending:'))
        proof = inspect_audio(self.path)
        verified = adapt_serato_frame(value, {str(self.path): proof})['A']
        self.assertEqual(verified['versionId'], proof['versionId'])
        self.assertTrue(verified['identityReady'])
        self.assertEqual(verified['positionMs'], 1250)
        self.assertEqual(verified['durationMs'], 2000)

    def test_same_path_replaced_file_immediately_loses_old_binding(self):
        value = frame()
        value['decks'][0]['audio_path'] = str(self.path)
        proof = inspect_audio(self.path)
        proof.update(audioContentId='pcm-sha256-v1:' + 'a' * 64, audioClockDurationMs=2000)
        self.write_wave(1)
        self.assertFalse(proof_is_current(proof, str(self.path)))
        raw = adapt_serato_frame(value, {str(self.path): proof})['A']
        self.assertFalse(raw['identityReady'])
        self.assertNotEqual(raw['versionId'], proof['versionId'])
        self.assertNotIn('audioContentId', raw)

    def lyric_timeline(self, version, text='合成歌词', duration=2000):
        return {'versionId': version, 'schemaVersion': 1, 'durationMs': duration,
                'quality': 'sentence', 'segments': [{'startMs': 0, 'endMs': 1500, 'text': text}]}

    def test_verified_pcm_reuses_other_filename_but_exact_version_has_priority(self):
        value = frame()
        value['decks'][0]['audio_path'] = str(self.path)
        proof = inspect_audio(self.path)
        proof.update(audioContentId='pcm-sha256-v1:' + 'a' * 64, audioClockDurationMs=2000)
        old = dict(proof, path=str(self.path.with_name('旧文件名.wav')), versionId='sha256:' + 'b' * 64)
        lyrics = {old['versionId']: self.lyric_timeline(old['versionId'])}
        raw = adapt_serato_frame(value, {str(self.path): proof})
        assign_serato_lyric_versions(raw, serato_lyric_index([old], lyrics), lyrics)
        self.assertEqual(raw['A']['lyricVersionId'], old['versionId'])
        self.assertEqual(raw['A']['lyricMatchReason'], 'content')
        self.assertEqual(raw['A']['versionId'], proof['versionId'])
        self.assertEqual(raw['A']['durationMs'], 2000)

        lyrics[proof['versionId']] = self.lyric_timeline(proof['versionId'], '已启用的新歌词')
        assign_serato_lyric_versions(raw, serato_lyric_index([old, proof], lyrics), lyrics)
        self.assertEqual(raw['A']['lyricVersionId'], proof['versionId'])

    def test_conflicting_pcm_equivalents_require_an_exact_version(self):
        value = frame()
        value['decks'][0]['audio_path'] = str(self.path)
        proof = inspect_audio(self.path)
        proof.update(audioContentId='pcm-sha256-v1:' + 'a' * 64, audioClockDurationMs=2000)
        old, copy_track = [dict(proof, versionId='sha256:' + char * 64) for char in ('b', 'c')]
        lyrics = {track['versionId']: self.lyric_timeline(track['versionId']) for track in (old, copy_track)}
        raw = adapt_serato_frame(value, {str(self.path): proof})
        assign_serato_lyric_versions(raw, serato_lyric_index([old, copy_track], lyrics), lyrics)
        self.assertEqual(raw['A']['lyricVersionId'], old['versionId'])

        lyrics[copy_track['versionId']] = self.lyric_timeline(copy_track['versionId'], '另一份歌词')
        assign_serato_lyric_versions(raw, serato_lyric_index([old, copy_track], lyrics), lyrics)
        self.assertIsNone(raw['A']['lyricVersionId'])
        self.assertEqual(raw['A']['lyricMatchReason'], 'ambiguous')

    def test_same_filename_without_verified_identity_cannot_select_lyrics(self):
        value = frame()
        value['decks'][0]['audio_path'] = str(self.path)
        proof = inspect_audio(self.path)
        proof.update(audioContentId='pcm-sha256-v1:' + 'a' * 64, audioClockDurationMs=2000)
        old = dict(proof, versionId='sha256:' + 'b' * 64, audioContentId='pcm-sha256-v1:' + 'b' * 64)
        lyrics = {old['versionId']: self.lyric_timeline(old['versionId'])}
        index = serato_lyric_index([old], lyrics)
        for proofs, reason in (({}, 'unverified'), ({str(self.path): proof}, 'missing')):
            raw = adapt_serato_frame(value, proofs)
            assign_serato_lyric_versions(raw, index, lyrics)
            self.assertEqual(raw['A']['lyricVersionId'], '')
            self.assertEqual(raw['A']['lyricMatchReason'], reason)

    def test_content_match_needs_same_decoded_clock_and_consistent_known_file_duration(self):
        value = frame()
        value['decks'][0]['audio_path'] = str(self.path)
        proof = inspect_audio(self.path)
        proof.update(audioContentId='pcm-sha256-v1:' + 'a' * 64, audioClockDurationMs=2000)
        old_version = 'sha256:' + 'b' * 64
        lyrics = {old_version: self.lyric_timeline(old_version, duration=2040)}

        def match(current, old_clock):
            old = dict(proof, versionId=old_version, audioClockDurationMs=old_clock)
            raw = adapt_serato_frame(value, {str(self.path): current})
            assign_serato_lyric_versions(raw, serato_lyric_index([old], lyrics), lyrics)
            return raw['A']['lyricVersionId'], raw['A']['lyricMatchReason']

        self.assertEqual(match(proof, 2000), ('', 'duration_mismatch'))
        self.assertEqual(match(dict(proof, durationMs=None), 2040), ('', 'missing'))
        self.assertEqual(match(dict(proof, durationMs=None), 2000), (old_version, 'content'))

    def test_measured_duration_conflict_rejects_even_exact_version_after_eof(self):
        value = frame()
        value['decks'][0].update(audio_path=str(self.path), position_seconds=2.5)
        proof = inspect_audio(self.path)
        lyrics = {proof['versionId']: self.lyric_timeline(proof['versionId'], duration=4000)}
        raw = adapt_serato_frame(value, {str(self.path): proof})
        self.assertEqual(raw['A']['durationMs'], 0)
        self.assertEqual(raw['A']['verifiedDurationMs'], 2000)
        assign_serato_lyric_versions(raw, {}, lyrics)
        self.assertEqual(raw['A']['lyricVersionId'], '')
        self.assertEqual(raw['A']['lyricMatchReason'], 'duration_mismatch')

    def test_program_accepts_unknown_duration_and_live_clock(self):
        from app.controller import initial_state
        sample = frame()
        raw = adapt_serato_frame(sample, {})
        state = initial_state()
        state.update(mode='serato', inputMode='serato')
        payload = {'state': state, 'raw': raw, 'timelines': {}}
        self.assertIs(validate_program_payload(payload), payload)


class DisplayPreferenceTests(unittest.TestCase):
    def test_preferences_reach_output_without_mutating_original(self):
        initial = dict(DEFAULT_STYLE)
        changed = changed_style(initial, 'fontSize', 72)
        self.assertEqual(changed['fontSize'], 72)
        self.assertEqual(initial['fontSize'], 48)
        self.assertEqual(validate_style(changed), changed)

    def test_bad_display_preferences_are_rejected(self):
        for key, value in (('fontSize', 200), ('brightness', float('inf')), ('letterSpacing', True),
                           ('sweepMode', 'unknown'), ('dynamicBackground', 1), ('fontFamily', ''),
                           ('unsupported', True)):
            with self.assertRaises(ValueError):
                changed_style(DEFAULT_STYLE, key, value)


if __name__ == '__main__':
    unittest.main()
