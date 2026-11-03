"""Source-time lyric queries and native-clock/controller integration."""
import copy
import unittest
from pathlib import Path

from app.lyrics import project_lyrics


def sample_timeline(quality='word', version='test:A'):
    return {'schemaVersion': 1, 'versionId': version, 'durationMs': 6000,
            'quality': quality, 'segments': [
                {'startMs': 1000, 'endMs': 2000, 'text': '甲乙', 'words': [
                    {'startMs': 1000, 'endMs': 1300, 'text': '甲'},
                    {'startMs': 1700, 'endMs': 2000, 'text': '乙'},
                ]},
                {'startMs': 4000, 'endMs': 5000, 'text': '丙', 'words': [
                    {'startMs': 4000, 'endMs': 5000, 'text': '丙'},
                ]},
            ]}


class LyricProjectionTests(unittest.TestCase):
    def setUp(self):
        self.timeline = sample_timeline()

    def test_missing_timeline_is_hidden(self):
        result = project_lyrics(None, 1500)
        self.assertFalse(result['visible'])
        self.assertEqual(result['text'], '')
        self.assertEqual(result['progress'], 0)
        self.assertEqual(result['quality'], 'unavailable')

    def test_sparse_gaps_hide_current_sentence_and_identify_next(self):
        for position, next_text in ((0, '甲乙'), (999, '甲乙'), (2000, '丙'), (3999, '丙')):
            with self.subTest(position=position):
                result = project_lyrics(self.timeline, position)
                self.assertFalse(result['visible'])
                self.assertEqual(result['text'], '')
                self.assertEqual(result['nextText'], next_text)

    def test_start_inclusive_end_exclusive_and_end_of_track_hidden(self):
        self.assertTrue(project_lyrics(self.timeline, 1000)['visible'])
        self.assertFalse(project_lyrics(self.timeline, 2000)['visible'])
        self.assertTrue(project_lyrics(self.timeline, 4000)['visible'])
        for position in (5000, 6000, 7000):
            result = project_lyrics(self.timeline, position)
            self.assertFalse(result['visible'])
            self.assertEqual(result['nextText'], '')

    def test_word_fill_tracks_source_time_and_holds_during_word_gap(self):
        for position, progress in ((1000, 0), (1150, .25), (1300, .5),
                                   (1500, .5), (1700, .5), (1850, .75)):
            with self.subTest(position=position):
                result = project_lyrics(self.timeline, position)
                self.assertEqual(result['text'], '甲乙')
                self.assertAlmostEqual(result['progress'], progress)

    def test_sentence_quality_does_not_invent_word_fill(self):
        result = project_lyrics(sample_timeline('sentence'), 1850)
        self.assertEqual(result['text'], '甲乙')
        self.assertEqual(result['quality'], 'sentence')
        self.assertEqual(result['progress'], 0)

    def test_pause_and_seek_queries_have_no_wall_clock_accumulator(self):
        before = copy.deepcopy(self.timeline)
        paused = project_lyrics(self.timeline, 1150)
        project_lyrics(self.timeline, 4500)
        self.assertEqual(project_lyrics(self.timeline, 1150), paused)
        self.assertEqual(project_lyrics(self.timeline, 1000)['progress'], 0)
        self.assertEqual(project_lyrics(self.timeline, 0)['text'], '')
        self.assertEqual(self.timeline, before)


class ClockToLyricIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # No QApplication, processes, hardware, or sleeps are needed for the
        # pure controller projection. The real shared C++ core must be built.
        from app.controller import initial_state, project_state
        from app.native import NativeClock
        cls.initial_state = staticmethod(initial_state)
        cls.project_state = staticmethod(project_state)
        cls.clock = NativeClock(Path(__file__).resolve().parents[1])

    def setUp(self):
        self.state = self.initial_state()
        self.state['activeDeck'] = 'A'
        self.timelines = {'test:A': sample_timeline()}
        self.deck = {'versionId': 'test:A', 'title': 'Real source version', 'path': '',
                     'positionMs': 1150.0, 'durationMs': 6000.0, 'speed': 1.0,
                     'playing': True, 'capturedNs': 1_000_000_000,
                     'validUntilNs': 1_350_000_000, 'generation': 0}

    def query(self, now, decks=None):
        self.state = self.project_state(self.state, decks or {'A': self.deck},
                                       self.timelines, self.clock, now)
        return self.state

    def test_pause_keeps_both_position_and_word_fill(self):
        self.deck['playing'] = False
        self.deck['speed'] = 1.2
        first = copy.deepcopy(self.query(1_000_000_000)['lyric'])
        later = self.query(1_300_000_000)
        self.assertEqual(later['deckA']['positionMs'], 1150.0)
        self.assertEqual(later['lyric'], first)

    def test_speed_adjusts_word_query_using_source_milliseconds(self):
        self.deck['positionMs'] = 1000
        self.deck['speed'] = 1.5
        result = self.query(1_100_000_000)
        self.assertEqual(result['deckA']['positionMs'], 1150.0)
        self.assertAlmostEqual(result['lyric']['progress'], .25)

    def test_hot_cue_and_loop_reset_requery_without_old_fill(self):
        self.deck['positionMs'] = 1850
        self.assertAlmostEqual(self.query(1_000_000_000)['lyric']['progress'], .75)
        self.deck.update(positionMs=1000, capturedNs=1_100_000_000,
                         validUntilNs=1_450_000_000, generation=1)
        result = self.query(1_100_000_000)
        self.assertEqual(result['lyric']['progress'], 0)
        self.deck.update(positionMs=0, capturedNs=1_200_000_000,
                         validUntilNs=1_550_000_000, generation=2)
        self.assertFalse(self.query(1_200_000_000)['lyric']['visible'])

    def test_expired_paused_snapshot_hides_lyrics(self):
        self.deck['playing'] = False
        self.assertTrue(self.query(1_350_000_000)['lyric']['visible'])
        result = self.query(1_350_000_001)
        self.assertFalse(result['lyric']['visible'])
        self.assertEqual(result['effectiveSource'], 'none')
        self.assertTrue(result['stale'])

    def test_manual_none_hides_and_unprepared_version_never_reuses_previous_lyric(self):
        self.assertTrue(self.query(1_000_000_000)['lyric']['visible'])
        self.state['selectedSource'] = 'none'
        self.assertFalse(self.query(1_000_000_000)['lyric']['visible'])
        self.state['selectedSource'] = 'A'
        self.deck['versionId'] = 'test:unprepared'
        result = self.query(1_000_000_000)
        self.assertFalse(result['lyric']['visible'])
        self.assertEqual(result['lyric']['text'], '')
        self.assertEqual(result['deckA']['quality'], '暂无歌词')

    def test_manual_source_is_independent_from_active_demo_deck(self):
        deck_b = dict(self.deck, versionId='test:B')
        self.timelines['test:B'] = sample_timeline(version='test:B')
        self.timelines['test:B']['segments'][0]['text'] = '丁戊'
        self.timelines['test:B']['segments'][0]['words'][0]['text'] = '丁'
        self.timelines['test:B']['segments'][0]['words'][1]['text'] = '戊'
        self.state['selectedSource'] = 'B'
        result = self.query(1_000_000_000, {'A': self.deck, 'B': deck_b})
        self.assertEqual(result['effectiveSource'], 'B')
        self.assertEqual(result['lyric']['text'], '丁戊')


if __name__ == '__main__':
    unittest.main()
