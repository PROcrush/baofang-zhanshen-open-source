"""Live integration boundaries using real temporary audio identities, no DJ writes."""
import copy
import json
from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from app.controller import Controller, initial_state, project_state
from app.messages import validate_program_payload
from app.native import NativeClock
from app.rekordbox_adapter import accept_rekordbox_frame, adapt_rekordbox_frame, validate_rekordbox_frame
from app.storage import inspect_audio
from probes.rekordbox_live import RekordboxLiveReader
from probes.rekordbox_reader import ReaderError
from test_controller_modes import FakeBridge, FakeSignal


NOW = 1_000_000_000


def frame(paths):
    return {'type': 'rekordbox_frame', 'mode': 'rekordbox', 'protocolVersion': 1,
            'epoch': 'rekordbox:test', 'sequence': 1, 'positionOnly': True, 'producerVerified': True,
            'capturedNs': NOW, 'validUntilNs': NOW + 350_000_000, 'fader': None,
            'decks': [{'deck': side, 'available': True, 'trackPath': str(path),
                       'positionSeconds': 1.25 if side == 'A' else 1.75,
                       'normalRate': 1.05, 'playRequested': True} for side, path in zip('AB', paths)]}


class RekordboxLiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(__file__).resolve().parents[1]
        self.paths, self.proofs, self.timelines = [], {}, {}
        for i, side in enumerate('AB'):
            path = Path(self.temporary.name) / (side + '.wav')
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                stream.writeframes(bytes([i, 0]) * 24000)
            proof = inspect_audio(path)
            self.paths.append(path)
            self.proofs[str(path)] = proof
            self.timelines[proof['versionId']] = {'schemaVersion': 1, 'versionId': proof['versionId'],
                'durationMs': 3000, 'quality': 'sentence',
                'segments': [{'startMs': 1000, 'endMs': 2000, 'text': '盘' + side}]}
        self.sample = frame(self.paths)
        self.state = initial_state()
        self.state.update(mode='rekordbox', inputMode='rekordbox', selectedSource='A')
        self.clock = NativeClock(self.root)

    def project(self, sample=None, now=NOW):
        raw = adapt_rekordbox_frame(sample or self.sample, self.proofs)
        return project_state(self.state, raw, self.timelines, self.clock, now)

    def test_position_only_clock_never_invents_progress_between_observations(self):
        first, later = self.project(), self.project(now=NOW + 300_000_000)
        self.assertEqual(first['deckA']['positionMs'], 1250)
        self.assertEqual(later['deckA']['positionMs'], 1250)
        self.assertEqual(first['lyric'], later['lyric'])
        self.assertEqual(first['lyric']['progress'], .25)
        self.assertTrue(first['deckA']['playing'])
        self.assertFalse(self.project(now=NOW + 350_000_001)['lyric']['visible'])

    def test_manual_ab_switch_and_seek_follow_each_verified_version(self):
        self.assertEqual(self.project()['lyric']['text'], '盘A')
        self.state['selectedSource'] = 'B'
        result = self.project()
        self.assertEqual(result['effectiveSource'], 'B')
        self.assertEqual(result['lyric']['text'], '盘B')
        self.assertEqual(result['lyric']['progress'], .75)
        self.sample['decks'][1]['positionSeconds'] = 1.1
        self.sample['decks'][1]['playRequested'] = False
        self.assertAlmostEqual(self.project()['lyric']['progress'], .1)

    def test_missing_changed_or_empty_deck_never_borrows_previous_lyrics(self):
        self.state['selectedSource'] = 'B'
        self.assertTrue(self.project()['lyric']['visible'])
        self.sample['decks'][1].update(available=False, trackPath='', positionSeconds=None,
                                       playRequested=False, normalRate=None)
        self.assertFalse(self.project()['lyric']['visible'])
        raw = adapt_rekordbox_frame(self.sample, self.proofs)
        validate_program_payload({'state': self.state, 'raw': raw, 'timelines': self.timelines})
        self.state['selectedSource'] = 'A'
        self.paths[0].unlink()
        self.assertFalse(self.project()['lyric']['visible'])

    def test_auto_without_mixer_data_hides_and_manual_selection_remains_available(self):
        self.state['selectedSource'] = 'auto'
        self.assertFalse(self.project()['lyric']['visible'])
        self.state['selectedSource'] = 'A'
        self.assertTrue(self.project()['lyric']['visible'])

    def test_protocol_rejects_experiments_bad_positions_and_partial_messages(self):
        for mutate in (lambda f: f.update(type='rekordbox_experimental_frame'),
                       lambda f: f.update(positionOnly=False),
                       lambda f: f.update(producerVerified=False),
                       lambda f: f['decks'][0].update(positionSeconds=float('nan')),
                       lambda f: f['decks'][0].update(positionSeconds=10**1000),
                       lambda f: f.update(fader={'normalized': 0, 'leftSource': [], 'rightSource': 'B'}),
                       lambda f: f['decks'][1].update(deck='A'),
                       lambda f: f.update(validUntilNs=NOW + 350_000_001),
                       lambda f: f.update(decks=f['decks'][:1])):
            with self.subTest(mutate=mutate):
                sample = copy.deepcopy(self.sample)
                mutate(sample)
                with self.assertRaises(ValueError):
                    validate_rekordbox_frame(sample)
        self.assertFalse(accept_rekordbox_frame(self.sample['epoch'], 1, self.sample))

    def test_renderer_rejects_extrapolating_rekordbox_payload(self):
        raw = adapt_rekordbox_frame(self.sample, self.proofs)
        payload = {'state': self.state, 'raw': raw, 'timelines': self.timelines}
        validate_program_payload(payload)
        raw['A'].pop('clockMode')
        with self.assertRaises(ValueError):
            validate_program_payload(payload)

    def harness(self):
        h = SimpleNamespace(_state=initial_state(), raw={}, timelines=self.timelines,
            bridge=FakeBridge(), stateChanged=FakeSignal(), runtime=Path(self.temporary.name),
            clock=self.clock, output=None, file_proofs=self.proofs, file_errors={},
            index_pending={}, failed_file_signatures={})
        h.notice = lambda value: h._state.update(notice=value)
        h.index_audio = lambda _: None
        for name in ('send', 'read_bridge', 'stopBridge', 'start_bridge', 'tick', 'setInputMode',
                     'connectRekordbox', 'refresh_preparation'):
            setattr(h, name, MethodType(getattr(Controller, name), h))
        return h

    def test_controller_reconnect_status_clear_and_read_only_controls(self):
        h = self.harness()
        with patch('app.controller.monotonic_ns', return_value=NOW):
            h.setInputMode('rekordbox')
            self.assertEqual(h.bridge.arguments, ['-m', 'probes.rekordbox_live'])
            h._state['selectedSource'] = 'A'
            h.bridge.queue(self.sample)
            h.read_bridge()
            h.tick()
            self.assertTrue(h._state['lyric']['visible'])
            for name, args in (('togglePlayback', ()), ('seek', (5,)), ('setSpeed', (1.1,)),
                               ('setDeck', ('B',)), ('loadTrack', ('demo:A', 'A'))):
                getattr(Controller, name)(h, *args)
            self.assertEqual(h.bridge.writes, [])
            h.bridge.queue({'type': 'rekordbox_status', 'detail': '连接中断'})
            h.read_bridge()
            h.tick()
            self.assertEqual(h.raw, {})
            self.assertFalse(h._state['lyric']['visible'])
            h.file_errors['changed.wav'] = 'changed while reading'
            h.failed_file_signatures['changed.wav'] = (1, 2)
            h.connectRekordbox()
            self.assertIsNone(h._state['bridgeEpoch'])
            self.assertEqual(h._state['selectedSource'], 'A')
            self.assertEqual(h.file_errors, {})
            self.assertEqual(h.failed_file_signatures, {})

    def test_auto_crossfader_switch_center_hold_and_manual_override(self):
        h = self.harness()
        with patch('app.controller.monotonic_ns', return_value=NOW):
            h.setInputMode('rekordbox')
            for seq, value, expected in ((1, 0, 'A'), (2, 1, 'B'), (3, .5, 'B')):
                sample = copy.deepcopy(self.sample)
                sample.update(sequence=seq, fader={'normalized': value, 'leftSource': 'A', 'rightSource': 'B'})
                h.bridge.queue(sample)
                h.read_bridge()
                h.tick()
                self.assertEqual(h._state['effectiveSource'], expected)
                self.assertEqual(h._state['selection']['selectionBasis'], 'crossfader')
            h._state['selectedSource'] = 'A'
            h.tick()
            self.assertEqual(h._state['effectiveSource'], 'A')
            sample.update(sequence=4, fader=None)
            h.bridge.queue(sample)
            h.read_bridge()
            h.tick()
            self.assertEqual(h._state['effectiveSource'], 'A')
            h._state['selectedSource'] = 'auto'
            h.tick()
            self.assertFalse(h._state['lyric']['visible'])

    def test_slow_live_capture_is_never_freshened_at_publish_time(self):
        fake = SimpleNamespace(read_frame=lambda: {})
        times = iter([NOW, NOW + 100_000_001])
        reader = RekordboxLiveReader(reader_factory=lambda: fake, clock=lambda: next(times))
        with self.assertRaisesRegex(ReaderError, 'capture_timeout'):
            reader.read_frame()


if __name__ == '__main__':
    unittest.main()
