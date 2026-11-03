"""Actual-file lyric binding through live-mode source selection and native clock."""
import copy
import tempfile
import time
import unittest
import wave
from pathlib import Path

from app.controller import initial_state, project_state
from app.native import NativeClock
from app.serato_adapter import adapt_serato_frame
from app.storage import inspect_audio
from app.styles import traditional_style, DEFAULT_STYLE
from test_serato_adapter import frame


class LiveProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'fixture.wav'
        with wave.open(str(self.path), 'wb') as stream:
            stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0' * 24000)
        proof = inspect_audio(self.path)
        self.frame = frame()
        self.frame['decks'][0]['audio_path'] = str(self.path)
        self.frame['decks'][0]['velocity'] = 1
        self.frame['fader']['normalized'] = 0
        self.now = self.frame['capturedNs']
        self.raw = adapt_serato_frame(self.frame, {str(self.path): proof})
        self.state = initial_state()
        self.state.update(mode='serato', inputMode='serato', videoFader=0,
                          faderValidUntilNs=self.frame['validUntilNs'])
        self.timeline = {'schemaVersion': 1, 'versionId': proof['versionId'], 'durationMs': 3000,
                         'quality': 'word', 'segments': [
                             {'startMs': 1000, 'endMs': 2000, 'text': '甲乙', 'words': [
                                 {'startMs': 1000, 'endMs': 1500, 'text': '甲'},
                                 {'startMs': 1500, 'endMs': 2000, 'text': '乙'}]}]}
        self.timelines = {proof['versionId']: self.timeline}
        self.clock = NativeClock(Path(__file__).resolve().parents[1])

    def tearDown(self):
        self.temp.cleanup()

    def project(self, now=None):
        self.state = project_state(self.state, self.raw, self.timelines, self.clock, now or self.now)
        return self.state

    def test_video_side_and_real_file_bound_lyrics_are_projected(self):
        state = self.project(self.now + 100_000_000)
        self.assertEqual(state['effectiveSource'], 'A')
        self.assertEqual(state['deckA']['positionMs'], 1350)
        self.assertAlmostEqual(state['lyric']['progress'], .35)
        self.assertEqual(state['selection']['audioVocalSource'], 'unverified')

    def test_unprepared_selected_deck_does_not_borrow_other_lyrics(self):
        self.assertTrue(self.project()['lyric']['visible'])
        self.state['videoFader'] = 1
        state = self.project()
        self.assertEqual(state['autoSide'], 'B')
        self.assertEqual(state['effectiveSource'], 'none')
        self.assertFalse(state['lyric']['visible'])

    def test_hysteresis_holds_video_side_after_missing_lyrics(self):
        self.state['videoFader'] = 1
        self.assertEqual(self.project()['autoSide'], 'B')
        self.state['videoFader'] = .5
        self.assertEqual(self.project()['autoSide'], 'B')
        self.state['selectedSource'] = 'A'
        self.assertEqual(self.project()['effectiveSource'], 'A')

    def test_expired_fader_or_file_replacement_hides_lyrics(self):
        self.assertTrue(self.project()['lyric']['visible'])
        self.state['faderValidUntilNs'] = self.now - 1
        self.assertFalse(self.project()['lyric']['visible'])
        self.state.update(faderValidUntilNs=self.now + 350_000_000, videoFader=0)
        self.path.unlink()
        self.assertFalse(self.project()['lyric']['visible'])

    def test_reverse_has_true_observed_position_but_no_lyrics(self):
        self.raw['A']['speed'] = -1
        state = self.project()
        self.assertEqual(state['deckA']['positionText'], '00:01.250')
        self.assertFalse(state['lyric']['visible'])
        self.assertEqual(state['selection']['reasonCode'], 'clock_reverse')

    def test_estimated_ktv_wipe_tracks_pause_speed_seek_and_expiry(self):
        self.state['style'] = traditional_style(DEFAULT_STYLE)
        self.timeline['quality'] = 'sentence'
        self.raw['A'].update(positionMs=1250, speed=0, playing=False)
        paused = self.project(self.now + 50_000_000)['lyric']
        self.assertEqual(paused, self.project(self.now + 200_000_000)['lyric'])
        self.assertEqual(paused['progress'], .25)
        self.assertEqual(paused['quality'], 'sentence')
        self.raw['A'].update(speed=1.1, playing=True)
        self.assertAlmostEqual(self.project(self.now + 200_000_000)['lyric']['progress'], .47)
        self.raw['A'].update(positionMs=1750, speed=0, playing=False)
        self.assertAlmostEqual(self.project(self.now + 200_000_000)['lyric']['progress'], .75)
        self.raw['A']['positionMs'] = 1100
        self.assertAlmostEqual(self.project(self.now + 200_000_000)['lyric']['progress'], .1)
        self.assertFalse(self.project(self.now + 350_000_001)['lyric']['visible'])


if __name__ == '__main__':
    unittest.main()
