import copy
import tempfile
import time
import unittest
import wave
from pathlib import Path

from app.messages import accept_serato_frame, validate_program_payload, validate_serato_frame
from app.serato_adapter import adapt_serato_frame, proof_is_current
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
        self.write_wave(1)
        self.assertFalse(proof_is_current(proof, str(self.path)))
        raw = adapt_serato_frame(value, {str(self.path): proof})['A']
        self.assertFalse(raw['identityReady'])
        self.assertNotEqual(raw['versionId'], proof['versionId'])

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
