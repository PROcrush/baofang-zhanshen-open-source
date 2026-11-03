"""Explicit local subtitles reach the live cache without a cloud request."""
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from app.controller import Controller, initial_state, project_state
from app.native import NativeClock
from app.local_subtitles import load_local_timeline
from app.storage import Library, inspect_audio
from app.serato_adapter import adapt_serato_frame
from test_controller_modes import NOW, live_frame


class LocalSubtitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio = self.root / '本地歌曲.wav'
        with wave.open(str(self.audio), 'wb') as stream:
            stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0' * 32000)
        self.track = inspect_audio(self.audio)
        self.srt = self.root / '本地歌曲.srt'
        self.srt.write_text('1\n00:00:00,500 --> 00:00:01,500\n本地字幕\n', encoding='utf-8')
        self.library = Library(self.root / 'runtime')
        self.library.add_track(self.track)
        sample = live_frame()
        sample['decks'][0]['audio_path'] = str(self.audio)
        state = initial_state()
        state.update(mode='serato', inputMode='serato', lyricAdjustmentDeck='A', selectedSource='A')
        self.h = SimpleNamespace(_state=state, raw=adapt_serato_frame(sample, {str(self.audio): self.track}),
                                 library=self.library, timelines={}, file_proofs={str(self.audio): self.track},
                                 notices=[], stateChanged=SimpleNamespace(emit=lambda: None))
        self.h.notice = self.h.notices.append
        self.h.refresh_library = lambda: None
        self.h.refresh_preparation = lambda: None
        self.h.index_audio = lambda path, callback=None: callback(inspect_audio(path))
        for name in ('recognition_track', 'importTimeline', 'chooseLocalSubtitles', 'bindLocalSubtitles'):
            if hasattr(Controller, name):
                setattr(self.h, name, MethodType(getattr(Controller, name), self.h))
        self.network = patch('socket.socket', side_effect=AssertionError('本地字幕不得访问网络')).start()

    def tearDown(self):
        self.assertEqual(self.network.call_count, 0)
        patch.stopall()
        self.library.close()
        self.temp.cleanup()

    def test_existing_library_import_accepts_srt_for_current_verified_song(self):
        with patch('app.controller.QFileDialog.getOpenFileName', return_value=(str(self.srt), '')):
            self.h.importTimeline()
        timeline = self.library.timeline(self.track['versionId'])
        self.assertIsNotNone(timeline, self.h.notices)
        self.assertEqual(timeline['quality'], 'sentence')
        self.assertEqual(timeline['segments'][0]['startMs'], 500)
        self.assertEqual(timeline['segments'][0]['endMs'], 1500)
        clock = NativeClock(Path(__file__).resolve().parents[1])
        state = project_state(self.h._state, self.h.raw, self.h.timelines, clock, NOW)
        self.assertEqual(state['deckA']['cacheState'], 'ready')
        self.assertEqual(state['lyric']['text'], '本地字幕')
        self.assertEqual(state['effectiveSource'], 'A')

    def test_import_survives_library_restart_and_keeps_original_file(self):
        original = self.srt.read_bytes()
        self.h.bindLocalSubtitles(str(self.srt), self.track, 'A')
        self.library.close()
        self.library = Library(self.root / 'runtime')
        self.assertEqual(self.library.timeline(self.track['versionId'])['segments'][0]['startMs'], 500)
        self.assertEqual(self.srt.read_bytes(), original)

    def test_enhanced_lrc_keeps_provided_word_spans(self):
        lrc = self.root / '提供时码.lrc'
        lrc.write_text('[00:00.500]<00:00.500>本地<00:01.000>字幕\n[00:01.500]\n', encoding='utf-8')
        payload = load_local_timeline(lrc, self.track)
        self.assertEqual(payload['quality'], 'word')
        self.assertEqual(payload['segments'][0]['words'][1], {'startMs': 1000, 'endMs': 1500, 'text': '字幕'})
        self.assertFalse(payload['acousticAccuracyVerified'])

    def test_other_versions_json_is_rejected_without_rebinding(self):
        payload = load_local_timeline(self.srt, self.track)
        payload['versionId'] = 'sha256:' + 'a' * 64
        path = self.root / '另一版本.json'
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, '版本不匹配'):
            load_local_timeline(path, self.track)

    def test_out_of_range_or_overlapping_srt_keeps_existing_cache(self):
        self.h.bindLocalSubtitles(str(self.srt), self.track, 'A')
        before = self.library.timeline(self.track['versionId'])
        self.srt.write_text('1\n00:00:03,000 --> 00:00:05,000\n超时\n', encoding='utf-8')
        self.h.bindLocalSubtitles(str(self.srt), self.track, 'A')
        self.assertEqual(self.library.timeline(self.track['versionId']), before)
        self.assertIn('超出音频范围', self.h.notices[-1])

    def test_switching_song_in_file_dialog_does_not_bind_old_selection(self):
        def selected(*_):
            self.h.raw['A']['versionId'] = 'sha256:' + 'a' * 64
            return str(self.srt), ''
        with patch('app.controller.QFileDialog.getOpenFileName', side_effect=selected):
            self.h.chooseLocalSubtitles('A')
        self.assertIsNone(self.library.timeline(self.track['versionId']))
        self.assertTrue(self.h.notices)

    def test_audio_changed_while_indexing_does_not_attach_to_new_version(self):
        callbacks = []
        self.h.index_audio = lambda path, callback: callbacks.append(callback)
        self.h.bindLocalSubtitles(str(self.srt), self.track, 'A')
        current = dict(self.track, versionId='sha256:' + 'b' * 64)
        callbacks[0](current)
        self.assertIsNone(self.library.timeline(self.track['versionId']))
        self.assertIn('新的音频版本', self.h.notices[-1])


if __name__ == '__main__':
    unittest.main()
