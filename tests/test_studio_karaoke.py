import copy
import tempfile
import unittest
from pathlib import Path

from app.studio import StudioController, validate_segments
from tests.test_studio import APPLICATION, ROOT


class StudioKaraokeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.studio = StudioController(ROOT, Path(self.temporary.name))
        self.studio._state['durationMs'] = 12000
        self.studio._state['segments'] = validate_segments([
            {'startMs': 4000, 'endMs': 8000, 'text': '星光照亮今晚', 'characters': [
                {'text': '星光', 'startMs': 4000, 'endMs': 5500},
                {'text': '照亮', 'startMs': 6500, 'endMs': 7200},
                {'text': '今晚', 'startMs': 7200, 'endMs': 8000}]},
            {'startMs': 9000, 'endMs': 11000, 'text': '一起唱'}], 12000)
        self.studio._emit()

    def tearDown(self):
        self.studio.shutdown(); APPLICATION.processEvents(); self.temporary.cleanup()

    def test_one_click_preset_preserves_timing_and_undoes_as_one_change(self):
        self.studio.setSetting('templateId', 'none')
        before = copy.deepcopy(self.studio._state['segments'])
        original = copy.deepcopy(self.studio._state['settings'])
        count = len(self.studio._undo)
        self.studio.applyKaraokeStyle()
        self.assertEqual(self.studio.state['settings']['templateId'], 'karaoke')
        self.assertEqual(self.studio.state['settings']['highlightColor'], '#FFE600')
        self.assertTrue(self.studio.state['settings']['fontBold'])
        self.assertEqual(self.studio.state['settings']['strokeWidth'], 3)
        for key in ('effectId', 'entranceId', 'exitId'):
            self.assertEqual(self.studio.state['settings'][key], 'none')
        self.assertEqual(self.studio.state['segments'], before)
        self.assertEqual(len(self.studio._undo), count + 1)
        self.studio.applyKaraokeStyle()
        self.assertEqual(len(self.studio._undo), count + 1)
        self.studio.undo()
        self.assertEqual(self.studio.state['settings'], original)
        self.assertEqual(self.studio.state['segments'], before)

    def test_simple_style_and_custom_font_colors_remain_available(self):
        self.studio.applyKaraokeStyle()
        self.studio.setSetting('highlightColor', '#F088AA')
        self.studio.applyKaraokeStyle('karaoke-card')
        self.assertEqual(self.studio.state['settings']['templateId'], 'karaoke-card')
        self.assertEqual(self.studio.state['settings']['highlightColor'], '#F088AA')
        self.studio.setSetting('templateId', 'karaoke')
        self.assertEqual(self.studio.state['settings']['highlightColor'], '#FFE600')

    def test_summary_and_current_quality_distinguish_sentence_only_lyrics(self):
        self.assertEqual(self.studio.state['karaokeTiming'],
                         {'total': 2, 'timed': 1, 'sentence': 1, 'character': 0})
        self.studio.seek(4700)
        self.assertEqual(self.studio.state['currentSegment'], 0)
        self.assertEqual(self.studio.state['currentTimingQuality'], 'word')
        self.studio.seek(8500)
        self.assertEqual(self.studio.state['currentSegment'], -1)
        self.assertEqual(self.studio.state['nextText'], '一起唱')
        self.studio.seek(9500)
        self.assertEqual(self.studio.state['currentSegment'], 1)
        self.assertEqual(self.studio.state['currentTimingQuality'], 'sentence')
        self.studio.updateSegment(0, '星光照亮这个夜晚')
        self.assertEqual(self.studio.state['karaokeTiming']['timed'], 0)

    def test_audio_sampling_requeries_source_clock_and_stops_on_pause(self):
        self.studio._state['playbackPlaying'] = True
        self.studio._playback_position(4700)
        self.assertEqual(self.studio.state['positionMs'], 4700)
        revision = self.studio.state['previewRevision']
        self.studio._playback_position(4700)
        self.assertEqual(self.studio.state['previewRevision'], revision)
        self.studio._playback_position(4200)
        self.assertEqual(self.studio.state['positionMs'], 4200)
        self.studio._state['playbackPlaying'] = False
        self.studio._playback_position(4800)
        self.assertEqual(self.studio.state['positionMs'], 4200)


if __name__ == '__main__':
    unittest.main()
