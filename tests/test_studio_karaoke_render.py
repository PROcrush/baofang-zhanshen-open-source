"""Source-time karaoke and actual final-font clipping, without audio/services."""
import copy
import math
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if sys.platform == 'darwin':
    from scripts.repair_qt_metadata import repair_qt_metadata
    repair_qt_metadata()

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QTextLayout

from app.karaoke import singing_start, token_cursor, traditional_lines
from worker.studio_render import _paint_line, _qt, draw_frame
from worker.style_catalog import DEFAULT_SETTINGS, TRADITIONAL_KARAOKE_STYLE

APPLICATION = _qt()
SETTINGS = dict(DEFAULT_SETTINGS, width=960, height=480, fontSize=64,
                templateId='karaoke', strokeWidth=0, shadow=False,
                textColor='#ffffff', highlightColor='#ff0000')


def pixels(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return rgba.constBits().tobytes()


def timed():
    return {'startMs': 0, 'endMs': 2000, 'text': 'AVfi 你好WWW', 'characters': [
        {'text': 'AVfi ', 'startMs': 100, 'endMs': 500},
        {'text': '你好WWW', 'startMs': 1200, 'endMs': 1800}]}


class KaraokeTimeTests(unittest.TestCase):
    def test_traditional_phrases_use_only_supplied_token_boundaries(self):
        words = ['梦里花开的香气', '那么甜蜜，', '想用支彩色铅笔', '画一片森林。']
        row = {'startMs': 900, 'endMs': 8000, 'text': ''.join(words), 'characters': [
            {'text': text, 'startMs': start, 'endMs': end} for text, start, end in
            zip(words, [1000,2500,4200,6500], [2000,3500,6000,7500])]}
        before = copy.deepcopy(row)
        lines = traditional_lines([row])
        self.assertEqual(len(lines), 2)
        self.assertEqual([(line['startMs'],line['endMs']) for line in lines], [(900,4200),(4200,8000)])
        self.assertEqual(''.join(line['text'] for line in lines), row['text'])
        self.assertEqual([token for line in lines for token in line['characters']], row['characters'])
        self.assertTrue(all(len(line['text']) <= 14 for line in lines))
        self.assertEqual(token_cursor(lines[0], 3600), token_cursor(lines[0], 4100))
        self.assertEqual(row, before)

    def test_sentence_and_large_single_token_are_not_given_new_clocks(self):
        text = '只保留真实词区间不会为了短句拆造时间' * 2
        row = {'startMs': 1000, 'endMs': 5000, 'text': text,
               'characters': [{'text':text,'startMs':1000,'endMs':5000}]}
        self.assertEqual(traditional_lines([row]), [row])
        sentence = dict(row); sentence.pop('characters')
        self.assertEqual(traditional_lines([sentence]), [sentence])

    def test_supplied_token_ranges_hold_in_gaps_and_seek_in_any_order(self):
        row = timed()
        original = copy.deepcopy(row)
        expected = {0: (0, 5, 0), 300: (0, 5, .5), 500: (5, 10, 0),
                    900: (5, 10, 0), 1200: (5, 10, 0), 1500: (5, 10, .5),
                    1800: (10, 10, 0)}
        for position in (1500, 0, 900, 300, 1800, 500, 1200, 300):
            self.assertEqual(token_cursor(row, position), expected[position])
        self.assertEqual(singing_start(row), 100)
        self.assertEqual(row, original)

    def test_incomplete_mismatched_and_invalid_tokens_downgrade(self):
        bad = []
        for tokens in (None, [], {}, [None], [{'text': 'missing', 'startMs': 0, 'endMs': 1}]):
            bad.append(dict(timed(), characters=tokens))
        for key, value in [('startMs', True), ('startMs', float('nan')), ('endMs', float('inf')),
                           ('endMs', 100), ('startMs', -1), ('endMs', 2200), ('text', '')]:
            row = timed()
            row['characters'][0][key] = value
            bad.append(row)
        row = timed(); row['characters'][1]['startMs'] = 400; bad.append(row)
        row = timed(); row['characters'].pop(); bad.append(row)
        bad.append({'startMs': 0, 'endMs': 1000, 'text': '句级'})
        for row in bad:
            with self.subTest(row=row):
                self.assertIsNone(token_cursor(row, 300))
                self.assertEqual(singing_start(row), row['startMs'])


class KaraokeDrawingTests(unittest.TestCase):
    def test_traditional_slots_keep_next_line_in_place_and_use_equal_sizes(self):
        rows = [{'startMs': index*3000,'endMs':index*3000+2800,'text':text}
                for index,text in enumerate(['第一句','第二句','第三句'])]
        settings = dict(SETTINGS, **TRADITIONAL_KARAOKE_STYLE)
        observed = []
        with patch('worker.studio_render._paint_line', side_effect=lambda *args,**kwargs:observed.append((args,kwargs))):
            draw_frame(rows, settings, 1000)
            second_preview = next(call for call in observed if call[0][1]=='第二句')
            first = next(call for call in observed if call[0][1]=='第一句')
            self.assertEqual(first[0][4], second_preview[0][4])
            observed.clear()
            draw_frame(rows, settings, 4000)
            second_active = next(call for call in observed if call[0][1]=='第二句')
            third_preview = next(call for call in observed if call[0][1]=='第三句')
            self.assertEqual(second_preview[0][2:5], second_active[0][2:5])
            self.assertEqual(second_active[0][8], 'right')
            self.assertEqual(third_preview[0][8], 'left')

    def test_traditional_yellow_white_outline_is_unboxed_over_the_background(self):
        settings = dict(SETTINGS, **TRADITIONAL_KARAOKE_STYLE)
        rows = [timed()]
        traditional = draw_frame(rows, settings, 1500, transparent=True)
        colors = [traditional.pixelColor(x,y) for y in range(480) for x in range(960)]
        self.assertTrue(any(c.alpha()>200 and c.red()>220 and c.green()>180 and c.blue()<80 for c in colors))
        self.assertTrue(any(c.alpha()>200 and min(c.red(),c.green(),c.blue())>225 for c in colors))
        self.assertTrue(any(c.alpha()>200 and max(c.red(),c.green(),c.blue())<40 for c in colors))
        self.assertEqual(traditional.pixelColor(500,300).alpha(), 0)
        card = draw_frame(rows, dict(settings,templateId='karaoke-card'), 1500, transparent=False)
        empty = draw_frame([], settings, 1500)
        classic = draw_frame(rows, settings, 1500)
        self.assertEqual(classic.pixelColor(500,300), empty.pixelColor(500,300))
        self.assertNotEqual(card.pixelColor(500,300), empty.pixelColor(500,300))

    def line_image(self, text, settings, max_width, **options):
        image = QImage(960, 480, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        _paint_line(painter, text, 480, 240, 72, settings, max_width, **options)
        painter.end()
        return image

    def test_final_bold_spaced_shrunk_glyph_boundaries_and_utf16_cursor(self):
        for text, first in [('AVfi 你好WWW', 'AVfi '), ('你好😀 WWW', '你好😀 ')]:
            with self.subTest(text=text):
                settings = dict(SETTINGS, letterSpacing=8, fontBold=True)
                row = {'startMs': 0, 'endMs': 1000, 'text': text, 'characters': [
                    {'text': first, 'startMs': 0, 'endMs': 400},
                    {'text': text[len(first):], 'startMs': 600, 'endMs': 1000}]}
                # Independent Qt layout supplies the expected boundary of the
                # complete final line, including kerning and fallback glyphs.
                font = QFont(settings['fontFamily']); font.setPixelSize(72)
                font.setWeight(QFont.Weight.Bold)
                font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 8)
                while QFontMetricsF(font).horizontalAdvance(text) > 230 and font.pixelSize() > 8:
                    font.setPixelSize(font.pixelSize() - 1)
                self.assertLess(font.pixelSize(), 72)
                metrics = QFontMetricsF(font)
                layout = QTextLayout(text, font)
                layout.beginLayout(); line = layout.createLine(); line.setLineWidth(10000); layout.endLayout()
                first_index = len(first.encode('utf-16-le')) // 2
                first_x = line.cursorToX(first_index)[0]
                total_x = metrics.horizontalAdvance(text)
                for position, edge in ((200, first_x / 2), (450, first_x),
                                       (800, first_x + (total_x - first_x) / 2)):
                    actual = self.line_image(text, settings, 230, highlight_cursor=token_cursor(row, position))
                    expected = self.line_image(text, settings, 230)
                    painter = QPainter(expected)
                    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
                    painter.translate(480, 240)
                    painter.setFont(font)
                    painter.setPen(QColor(settings['highlightColor']))
                    painter.setClipRect(QRectF(-total_x / 2, -metrics.height(), edge, metrics.height() * 2))
                    painter.drawText(QPointF(-total_x / 2, (metrics.ascent() - metrics.descent()) / 2), text)
                    painter.end()
                    self.assertTrue(pixels(actual) == pixels(expected), (text, position, edge))

    def test_typewriter_clip_contains_even_fully_highlighted_text(self):
        text = 'MMMMMMMMMMMM'
        base = self.line_image(text, SETTINGS, 800, reveal=.2)
        highlighted = self.line_image(text, SETTINGS, 800, reveal=.2,
                                      highlight_cursor=(len(text), len(text), 0))

        def visible_right(image):
            return max(x for y in range(image.height()) for x in range(image.width())
                       if image.pixelColor(x, y).alpha())

        self.assertEqual(visible_right(highlighted), visible_right(base))
        self.assertLess(visible_right(highlighted), 480)

    def test_word_gap_freezes_actual_pixels_and_backward_seek_is_exact(self):
        row = timed()
        for transparent in (False, True):
            gap = pixels(draw_frame([row], SETTINGS, 600, transparent=transparent))
            self.assertEqual(gap, pixels(draw_frame([row], SETTINGS, 1100, transparent=transparent)))
            forward = pixels(draw_frame([row], SETTINGS, 1500, transparent=transparent))
            self.assertNotEqual(gap, forward)
            self.assertEqual(gap, pixels(draw_frame([row], SETTINGS, 600, transparent=transparent)))

    def test_sentence_and_broken_tokens_use_the_same_estimated_line_wipe(self):
        row = timed()
        sentence = dict(row); sentence.pop('characters')
        reference = pixels(draw_frame([sentence], SETTINGS, 400))
        self.assertNotEqual(reference, pixels(draw_frame([sentence], SETTINGS, 1500)))
        self.assertEqual(reference, pixels(draw_frame([sentence], SETTINGS, 400)))
        for change in ('missing', 'zero', 'nan', 'overlap'):
            bad = copy.deepcopy(row)
            if change == 'missing':
                bad['characters'].pop()
            elif change == 'zero':
                bad['characters'][0]['endMs'] = bad['characters'][0]['startMs']
            elif change == 'nan':
                bad['characters'][0]['startMs'] = math.nan
            else:
                bad['characters'][1]['startMs'] = 400
            self.assertEqual(reference, pixels(draw_frame([bad], SETTINGS, 400)))

    def test_current_next_gap_preview_without_countdown_and_sentence_boundary(self):
        rows = [{'startMs': 6000, 'endMs': 8000, 'text': '第一句'},
                {'startMs': 9000, 'endMs': 11000, 'text': '第二句'}]
        calls = []

        def record(painter, text, *args, **options):
            calls.append(text)

        with patch('worker.studio_render._paint_line', side_effect=record):
            draw_frame(rows, SETTINGS, 6500)
            self.assertEqual(calls, ['第一句', '第二句'])
            calls.clear()
            draw_frame(rows, SETTINGS, 8000)
            self.assertEqual(calls, ['第二句'])
            calls.clear()
            draw_frame(rows, SETTINGS, 9000)
            self.assertEqual(calls, ['第二句'])
        for template in ('karaoke', 'karaoke-card'):
            settings = dict(SETTINGS, templateId=template)
            reference = pixels(draw_frame(rows, settings, 0))
            for position in (1000, 2000, 3000, 4000, 5000, 5999):
                with self.subTest(template=template, position=position):
                    self.assertEqual(reference, pixels(draw_frame(rows, settings, position)))
        self.assertEqual(pixels(draw_frame(rows, SETTINGS, 11000)), pixels(draw_frame([], SETTINGS, 11000)))


if __name__ == '__main__':
    unittest.main()
