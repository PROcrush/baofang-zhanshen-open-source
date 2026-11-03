"""AX identity is an explicit local choice; coarse UI titles never bind files."""
import copy
from pathlib import Path
import tempfile
import unittest
import wave

from app.controller import initial_state, project_state
from app.native import NativeClock
from app.rekordbox_ax_adapter import (accept_ax_frame, adapt_ax_frame, make_ax_binding,
                                      validate_ax_frame)
from app.storage import inspect_audio


NOW = 1_000_000_000


def frame():
    return {'type': 'rekordbox_ax_frame', 'mode': 'rekordbox_ax', 'protocolVersion': 1,
            'sourceKind': 'accessibility', 'producerVerified': True, 'positionOnly': True,
            'epoch': 'rekordbox-ax:test', 'sequence': 1, 'capturedNs': NOW,
            'validUntilNs': NOW + 350_000_000, 'fader': None,
            'decks': [{'deck': 'A', 'available': True, 'title': '同名歌曲 Remix',
                       'positionSeconds': 1.2, 'remainingSeconds': 1.8,
                       'normalRate': 1, 'playRequested': False, 'observationId': 'deck-a-one'},
                      {'deck': 'B', 'available': True, 'title': '同名歌曲 Remix',
                       'positionSeconds': 1.7, 'remainingSeconds': 2.3,
                       'normalRate': 1, 'playRequested': False, 'observationId': 'deck-b-one'}]}


class RekordboxAXAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(__file__).resolve().parents[1]
        self.paths, self.proofs, self.timelines = [], {}, {}
        for i, seconds in enumerate((3, 4, 3)):
            path = (Path(self.temporary.name) / f'actual-file-{i}.wav').resolve()
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                stream.writeframes(bytes([i, 0]) * 8000 * seconds)
            proof = inspect_audio(path)
            self.paths.append(path)
            self.proofs[str(path)] = proof
            self.timelines[proof['versionId']] = {
                'schemaVersion': 1, 'versionId': proof['versionId'], 'durationMs': seconds * 1000,
                'quality': 'sentence',
                'segments': [{'startMs': 1000, 'endMs': 2000, 'text': f'文件{i}歌词'}]}
        self.sample = frame()
        self.bindings = {side: make_ax_binding(self.sample, side, self.proofs[str(path)])
                         for side, path in zip('AB', self.paths)}
        self.clock = NativeClock(self.root)
        self.state = initial_state()
        # Exercise the shared live projection/clock contract, independently of
        # the controller's AX reader dispatch and UI work owned by another task.
        self.state.update(mode='rekordbox', inputMode='rekordbox', selectedSource='A')

    def raw(self, sample=None, bindings=None):
        return adapt_ax_frame(self.sample if sample is None else sample,
                              self.bindings if bindings is None else bindings, self.proofs)

    def project(self, sample=None, *, now=NOW, bindings=None):
        return project_state(self.state, self.raw(sample, bindings), self.timelines, self.clock, now)

    def test_unbound_title_is_visible_but_never_guesses_a_matching_file(self):
        raw = self.raw(bindings={})
        self.assertEqual(raw['A']['title'], '同名歌曲 Remix')
        self.assertEqual(raw['A']['positionMs'], 1200)
        self.assertEqual(raw['A']['path'], '')
        self.assertEqual(raw['A']['bindingStatus'], 'unbound')
        self.assertFalse(raw['A']['identityReady'])
        self.assertIsNone(raw['A']['identityProof'])
        self.assertTrue(raw['A']['versionId'].startswith('pending:ax:'))
        self.assertNotEqual(raw['A']['versionId'], raw['B']['versionId'])
        self.assertFalse(self.project(bindings={})['lyric']['visible'])

    def test_explicit_file_may_have_different_filename_from_observed_title(self):
        raw = self.raw()
        self.assertEqual(raw['A']['bindingStatus'], 'ready')
        self.assertTrue(raw['A']['identityReady'])
        self.assertEqual(raw['A']['path'], str(self.paths[0]))
        self.assertNotEqual(self.paths[0].stem, self.sample['decks'][0]['title'])
        self.assertEqual(raw['A']['identityProof']['size'], self.paths[0].stat().st_size)

    def test_manual_ab_switch_uses_independent_exact_file_versions(self):
        first = self.project()
        self.assertEqual(first['effectiveSource'], 'A')
        self.assertEqual(first['lyric']['text'], '文件0歌词')
        self.assertAlmostEqual(first['lyric']['progress'], .2)
        self.state['selectedSource'] = 'B'
        second = self.project()
        self.assertEqual(second['effectiveSource'], 'B')
        self.assertEqual(second['lyric']['text'], '文件1歌词')
        self.assertAlmostEqual(second['lyric']['progress'], .7)

    def test_clock_holds_observation_and_expiration_hides_lyrics(self):
        first, later = self.project(), self.project(now=NOW + 300_000_000)
        self.assertEqual(first['deckA']['positionMs'], later['deckA']['positionMs'])
        self.assertEqual(first['lyric'], later['lyric'])
        self.assertEqual(self.raw()['A']['clockMode'], 'observed')
        self.assertFalse(self.raw()['A']['playing'])
        self.assertFalse(self.project(now=NOW + 350_000_001)['lyric']['visible'])
        self.assertFalse(self.project(now=NOW - 1)['lyric']['visible'])

    def test_seek_and_loop_use_observed_positions_without_resetting_binding(self):
        for position in (1.8, 1.1, 1.5):
            self.sample['decks'][0].update(positionSeconds=position, remainingSeconds=3 - position)
            result = self.project()
            self.assertTrue(result['lyric']['visible'])
            self.assertAlmostEqual(result['lyric']['progress'], position - 1)

    def test_different_title_observation_or_epoch_invalidates_only_its_binding(self):
        for mutate in (lambda f: f['decks'][0].update(title='另一个编辑版本'),
                       lambda f: f['decks'][0].update(observationId='deck-a-new'),
                       lambda f: f.update(epoch='rekordbox-ax:reconnected')):
            with self.subTest(mutate=mutate):
                sample = copy.deepcopy(self.sample)
                mutate(sample)
                raw = self.raw(sample)
                self.assertFalse(raw['A']['identityReady'])
                self.assertEqual(raw['A']['bindingStatus'], 'stale')
                self.assertEqual(raw['A']['path'], '')
                self.assertFalse(self.project(sample)['lyric']['visible'])
                if sample['epoch'] == self.sample['epoch']:
                    self.assertTrue(raw['B']['identityReady'])

    def test_same_title_with_wrong_duration_cannot_reuse_or_create_binding(self):
        with self.assertRaisesRegex(ValueError, '时长'):
            make_ax_binding(self.sample, 'A', self.proofs[str(self.paths[1])])
        sample = copy.deepcopy(self.sample)
        sample['decks'][0]['remainingSeconds'] += .3
        self.assertFalse(self.raw(sample)['A']['identityReady'])
        self.assertFalse(self.project(sample)['lyric']['visible'])
        self.assertTrue(self.raw(sample)['B']['identityReady'])

    def test_quantized_duration_tolerance_does_not_change_exact_cache_duration(self):
        sample = copy.deepcopy(self.sample)
        sample['decks'][0]['remainingSeconds'] += .25
        raw = self.raw(sample)
        self.assertTrue(raw['A']['identityReady'])
        self.assertEqual(raw['A']['durationMs'], 3000)
        self.assertTrue(self.project(sample)['lyric']['visible'])
        sample['decks'][0]['remainingSeconds'] += .001
        self.assertFalse(self.raw(sample)['A']['identityReady'])

    def test_final_quantized_tick_does_not_forge_file_duration(self):
        self.sample['decks'][0].update(positionSeconds=3.1, remainingSeconds=0)
        raw = self.raw()
        self.assertTrue(raw['A']['identityReady'])
        self.assertEqual(raw['A']['durationMs'], 0)
        self.assertFalse(self.project()['lyric']['visible'])

    def test_modified_or_removed_file_hides_lyrics_immediately(self):
        self.assertTrue(self.project()['lyric']['visible'])
        self.paths[0].write_bytes(self.paths[0].read_bytes() + b'changed')
        self.assertFalse(self.raw()['A']['identityReady'])
        self.assertFalse(self.project()['lyric']['visible'])
        self.assertTrue(self.raw()['B']['identityReady'])
        self.paths[1].unlink()
        self.assertFalse(self.raw()['B']['identityReady'])

    def test_proof_version_must_equal_confirmed_version_even_when_title_and_length_match(self):
        # A different 3s recording with the same AX title remains a separate
        # explicit choice; rebinding it requires a new confirmation.
        replacement = self.proofs[str(self.paths[2])]
        changed = copy.deepcopy(self.bindings)
        changed['A']['path'] = replacement['path']
        self.assertFalse(self.raw(bindings=changed)['A']['identityReady'])
        changed['A'] = make_ax_binding(self.sample, 'A', replacement)
        self.assertEqual(self.raw(bindings=changed)['A']['versionId'], replacement['versionId'])

    def test_unavailable_deck_is_absent_without_hiding_other_deck(self):
        self.sample['decks'][1].update(available=False, title='', observationId='',
                                        positionSeconds=None, remainingSeconds=None, normalRate=None)
        raw = self.raw()
        self.assertEqual(set(raw), {'A'})
        self.assertTrue(self.project()['lyric']['visible'])
        self.state['selectedSource'] = 'B'
        self.assertFalse(self.project()['lyric']['visible'])
        with self.assertRaisesRegex(ValueError, '播放观测'):
            make_ax_binding(self.sample, 'B', self.proofs[str(self.paths[1])])

    def test_protocol_rejects_wrong_source_bad_types_ranges_and_memory_protocol(self):
        for mutate in (lambda f: f.update(type='rekordbox_frame'),
                       lambda f: f.update(type='rekordbox_experimental_frame'),
                       lambda f: f.update(mode='rekordbox'),
                       lambda f: f.update(sourceKind='memory'),
                       lambda f: f.update(protocolVersion=True),
                       lambda f: f.update(positionOnly=False),
                       lambda f: f.update(producerVerified=False),
                       lambda f: f.update(epoch='rekordbox:old-memory'),
                       lambda f: f.update(epoch='rekordbox-ax:'),
                       lambda f: f.update(sequence=True),
                       lambda f: f.update(capturedNs=10 ** 1000),
                       lambda f: f.update(validUntilNs=NOW),
                       lambda f: f.update(validUntilNs=NOW + 350_000_001),
                       lambda f: f.update(fader={'normalized': 0}),
                       lambda f: f['decks'][0].update(deck=[]),
                       lambda f: f['decks'][1].update(deck='A'),
                       lambda f: f['decks'][0].update(available=1),
                       lambda f: f['decks'][0].update(playRequested=True),
                       lambda f: f['decks'][0].update(positionSeconds=True),
                       lambda f: f['decks'][0].update(positionSeconds=-.1),
                       lambda f: f['decks'][0].update(positionSeconds=float('nan')),
                       lambda f: f['decks'][0].update(positionSeconds=10 ** 1000),
                       lambda f: f['decks'][0].update(remainingSeconds=float('inf')),
                       lambda f: f['decks'][0].update(remainingSeconds=86400),
                       lambda f: f['decks'][0].update(normalRate={}),
                       lambda f: f['decks'][0].update(title=' '),
                       lambda f: f['decks'][0].update(title='bad\0title'),
                       lambda f: f['decks'][0].update(title='bad\ud800title'),
                       lambda f: f['decks'][0].update(observationId=[]),
                       lambda f: f['decks'][0].update(available=False),
                       lambda f: f.update(decks=f['decks'][:1])):
            with self.subTest(mutate=mutate):
                sample = copy.deepcopy(self.sample)
                mutate(sample)
                with self.assertRaises(ValueError):
                    validate_ax_frame(sample)

    def test_sequence_rejects_repeats_and_accepts_new_epoch(self):
        self.assertFalse(accept_ax_frame(self.sample['epoch'], 1, self.sample))
        self.assertFalse(accept_ax_frame(self.sample['epoch'], 2, self.sample))
        self.assertTrue(accept_ax_frame(self.sample['epoch'], 0, self.sample))
        self.assertTrue(accept_ax_frame('rekordbox-ax:previous', 1000, self.sample))

    def test_binding_rejects_unknown_duration_invalid_version_and_stale_proof(self):
        original = self.proofs[str(self.paths[0])]
        for mutate in (lambda p: p.update(durationMs=None),
                       lambda p: p.update(durationMs=True),
                       lambda p: p.update(durationMs=10 ** 1000),
                       lambda p: p.update(versionId='pending:file'),
                       lambda p: p.update(mtimeNs=0),
                       lambda p: p.update(path='relative.wav')):
            with self.subTest(mutate=mutate):
                proof = copy.deepcopy(original)
                mutate(proof)
                with self.assertRaises(ValueError):
                    make_ax_binding(self.sample, 'A', proof)
        for malformed in (None, 'file.wav', [], {}):
            with self.assertRaises(ValueError):
                make_ax_binding(self.sample, 'A', malformed)


if __name__ == '__main__':
    unittest.main()
