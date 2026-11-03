"""Live and studio KTV agree on source-time slots and final rendered pixels."""
import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from app.karaoke import lyric_frame, traditional_lines
from app.live_karaoke import LiveKaraokeItem
from app.lyrics import project_lyrics
from app.styles import DEFAULT_STYLE, traditional_style
from worker.studio_render import _qt, draw_frame
from worker.style_catalog import DEFAULT_SETTINGS, TRADITIONAL_KARAOKE_STYLE

APPLICATION = _qt()


def timeline():
    return {'quality': 'word', 'segments': [
        {'startMs': 2000, 'endMs': 6000, 'text': 'AVfi 你好WWW', 'words': [
            {'text': 'AVfi ', 'startMs': 2200, 'endMs': 3200},
            {'text': '你好WWW', 'startMs': 4200, 'endMs': 5800}]},
        {'startMs': 7000, 'endMs': 9000, 'text': '下一句', 'words': [
            {'text': '下一句', 'startMs': 7000, 'endMs': 8800}]},
        {'startMs': 9500, 'endMs': 11000, 'text': '第三句', 'words': [
            {'text': '第三句', 'startMs': 9500, 'endMs': 10900}]}]}


def rgba(image):
    return image.convertToFormat(QImage.Format.Format_RGBA8888).constBits().tobytes()


class LiveKaraokeTests(unittest.TestCase):
    def test_sentence_captions_sweep_from_source_time_without_inventing_words(self):
        source = {'quality': 'sentence', 'segments': [
            {'text': '像传统KTV一样走带', 'startMs': 1000, 'endMs': 5000},
            {'text': '下一句提前看见', 'startMs': 7000, 'endMs': 9000}]}
        before = copy.deepcopy(source)
        item = LiveKaraokeItem()
        item.settings = traditional_style(DEFAULT_STYLE)
        images = []
        item.setWidth(854); item.setHeight(480)
        for position, progress in ((0, 0), (1000, 0), (3000, .5), (4500, .875), (3000, .5)):
            projected = project_lyrics(source, position, traditional=True)
            self.assertAlmostEqual(projected['progress'], progress)
            self.assertEqual(projected['quality'], 'sentence')
            item.lyric = projected
            self.assertTrue(item.canSweep)
            image = QImage(854, 480, QImage.Format.Format_RGBA8888)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            try:
                item.paint(painter)
            finally:
                painter.end()
            images.append(rgba(image))
            settings = dict(DEFAULT_SETTINGS, **TRADITIONAL_KARAOKE_STYLE,
                            width=854, height=480, templateId='karaoke', fadeMs=0)
            self.assertEqual(images[-1], rgba(draw_frame(source['segments'], settings, position, transparent=True)))
        self.assertNotEqual(images[1], images[2])
        self.assertEqual(images[2], images[4])
        self.assertEqual(source, before)
        self.assertEqual(project_lyrics(source, 6000, traditional=True)['progress'], 0)
        for position in (3000, 4500):
            item.lyric = project_lyrics(source, position, traditional=True)
            item.settings = dict(item.settings, sweepMode='sentence')
            self.assertFalse(item.canSweep)
            image = QImage(854, 480, QImage.Format.Format_RGBA8888)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            try:
                item.paint(painter)
            finally:
                painter.end()
            self.assertEqual(rgba(image), images[0])

    def test_intro_and_gap_predisplay_keep_original_onsets_without_countdown(self):
        original = timeline()
        before = copy.deepcopy(original)
        for position, index, waiting in ((0, 0, True), (2100, 0, True), (2200, 0, False),
                                         (6000, 1, True), (7000, 1, False), (9000, 2, True)):
            with self.subTest(position=position):
                projected = project_lyrics(original, position, traditional=True)
                self.assertTrue(projected['visible'])
                self.assertEqual(projected['countdownMs'], 0)
                self.assertEqual(projected['waiting'], waiting)
                self.assertEqual(projected['karaoke']['lineIndex'], index)
                self.assertEqual(projected['karaoke']['current']['startMs'], original['segments'][index]['startMs'])
        self.assertFalse(project_lyrics(original, 11000, traditional=True)['visible'])
        self.assertEqual(original, before)

    def test_seek_and_pause_are_pure_and_word_gap_does_not_continue_sweep(self):
        lyrics = timeline()
        self.assertEqual(project_lyrics(lyrics, 4000, traditional=True)['progress'],
                         project_lyrics(lyrics, 3300, traditional=True)['progress'])
        expected = project_lyrics(lyrics, 2700, traditional=True)
        for position in (7700, 0, 4600, 2700):
            project_lyrics(lyrics, position, traditional=True)
        self.assertEqual(expected, project_lyrics(lyrics, 2700, traditional=True))

    def test_sentence_and_invalid_tokens_use_estimation_without_claiming_word_precision(self):
        for invalid in ('sentence', 'incomplete', 'mismatched'):
            lyrics = timeline()
            if invalid == 'sentence':
                lyrics['quality'] = 'sentence'
            elif invalid == 'incomplete':
                lyrics['segments'][0]['words'].pop()
            else:
                lyrics['segments'][0]['words'][0]['text'] = 'wrong text'
            projected = project_lyrics(lyrics, 2700, traditional=True)
            self.assertEqual(projected['quality'], 'sentence')
            item = LiveKaraokeItem()
            item.settings = traditional_style(DEFAULT_STYLE)
            item.lyric = projected
            self.assertTrue(item.canSweep)
            self.assertEqual(projected['sweepTiming'], 'sentence')

    def test_live_long_lines_use_the_same_real_token_boundaries_as_studio(self):
        words = ['梦里花开的香气', '那么甜蜜，', '想用支彩色铅笔', '画一片森林。']
        row = {'startMs': 900, 'endMs': 8000, 'text': ''.join(words), 'words': [
            {'text': text, 'startMs': start, 'endMs': end} for text, start, end in
            zip(words, [1000, 2500, 4200, 6500], [2000, 3500, 6000, 7500])]}
        rows = traditional_lines([dict(row, characters=row['words'])])
        for position in (900, 3000, 4199, 4200, 7000):
            live = project_lyrics({'quality':'word', 'segments':[row]}, position, traditional=True)['karaoke']
            studio = lyric_frame(rows, position, pre_display=True)
            self.assertEqual(live, studio)

    def test_live_and_studio_pixels_match_through_sweep_gaps_and_line_changes(self):
        source = timeline()
        rows = [dict(row, characters=row['words']) for row in source['segments']]
        for width, height in ((854, 480), (1120, 720)):
            settings = dict(DEFAULT_SETTINGS, **TRADITIONAL_KARAOKE_STYLE,
                            width=width, height=height, templateId='karaoke', fadeMs=0)
            item = LiveKaraokeItem()
            item.setWidth(width); item.setHeight(height)
            item.settings = traditional_style(DEFAULT_STYLE)
            for position in (0, 2700, 3300, 4000, 5000, 6000, 7200, 9000):
                with self.subTest(size=(width, height), position=position):
                    item.lyric = project_lyrics(source, position, traditional=True)
                    image = QImage(width, height, QImage.Format.Format_RGBA8888)
                    image.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(image)
                    try:
                        item.paint(painter)
                    finally:
                        painter.end()
                    self.assertEqual(rgba(image), rgba(draw_frame(rows, settings, position, transparent=True)))
                    self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    def test_next_line_keeps_its_slot_and_nominal_size_when_it_becomes_current(self):
        item = LiveKaraokeItem()
        item.setWidth(854); item.setHeight(480)
        item.settings = traditional_style(DEFAULT_STYLE)
        observed = []
        def record(*args, **kwargs):
            observed.append((args[1], args[2], args[3], args[4], args[8]))
        for position in (2700, 7200):
            item.lyric = project_lyrics(timeline(), position, traditional=True)
            image = QImage(854, 480, QImage.Format.Format_RGBA8888)
            painter = QPainter(image)
            try:
                with patch('worker.studio_render._paint_line', side_effect=record):
                    item.paint(painter)
            finally:
                painter.end()
        first_next = next(row for row in observed[:2] if row[0] == '下一句')
        next_current = next(row for row in observed[2:] if row[0] == '下一句')
        self.assertEqual(first_next, next_current)
        self.assertEqual(observed[0][3], observed[1][3])

    def test_static_examples_have_no_sweep_and_sentence_option_keeps_slots(self):
        item = LiveKaraokeItem()
        item.settings = traditional_style(DEFAULT_STYLE)
        item.lyric = {'text':'静态示例', 'nextText':'下一句', 'quality':'word'}
        self.assertFalse(item.canSweep)
        item.lyric = project_lyrics(timeline(), 2700, traditional=True)
        self.assertTrue(item.canSweep)
        item.settings = dict(item.settings, sweepMode='sentence')
        self.assertFalse(item.canSweep)


if __name__ == '__main__':
    unittest.main()
