import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.subtitle_export import export_lrc, export_srt, write_subtitles


def timeline():
    return {'schemaVersion': 1, 'versionId': 'sha256:' + 'a' * 64, 'durationMs': 4000,
            'quality': 'sentence', 'segments': [
                {'startMs': 1000, 'endMs': 1500, 'text': '往事随风'},
                {'startMs': 2500, 'endMs': 3500, 'text': '下一句歌词'},
            ]}


class SubtitleExportTests(unittest.TestCase):
    def test_srt_preserves_silent_gap_and_original_text(self):
        source = timeline()
        before = copy.deepcopy(source)
        self.assertEqual(export_srt(source),
                         '1\n00:00:01,000 --> 00:00:01,500\n往事随风\n\n'
                         '2\n00:00:02,500 --> 00:00:03,500\n下一句歌词\n')
        self.assertEqual(source, before)

    def test_lrc_has_millisecond_timestamps_and_gap_clear_markers(self):
        self.assertEqual(export_lrc(timeline()),
                         '[00:01.000]往事随风\n[00:01.500]\n'
                         '[00:02.500]下一句歌词\n[00:03.500]\n')

    def test_adjacent_lrc_lines_do_not_insert_duplicate_clear_timestamp(self):
        source = timeline()
        source['segments'][1]['startMs'] = 1500
        result = export_lrc(source)
        self.assertEqual(result.count('[00:01.500]'), 1)
        self.assertIn('[00:01.500]下一句歌词', result)

    def test_half_up_milliseconds_and_minute_hour_rollover(self):
        source = timeline()
        source['durationMs'] = 3602000
        source['segments'] = [{'startMs': 59999.5, 'endMs': 60000.5, 'text': '一分钟'},
                              {'startMs': 3599999.5, 'endMs': 3601000.5, 'text': '一小时'}]
        srt = export_srt(source)
        self.assertIn('00:01:00,000 --> 00:01:00,001', srt)
        self.assertIn('01:00:00,000 --> 01:00:01,001', srt)
        lrc = export_lrc(source)
        self.assertIn('[01:00.000]一分钟', lrc)
        self.assertIn('[60:00.000]一小时', lrc)

    def test_negative_nonfinite_reversed_overlap_and_out_of_duration_refused(self):
        changes = [('startMs', -1), ('startMs', float('nan')), ('endMs', float('inf')),
                   ('startMs', 1600), ('endMs', 4001)]
        for key, value in changes:
            source = timeline()
            source['segments'][0][key] = value
            for export in (export_srt, export_lrc):
                with self.subTest(key=key, value=value, export=export.__name__), self.assertRaises(ValueError):
                    export(source)
        source = timeline()
        source['segments'][1]['startMs'] = 1499
        with self.assertRaises(ValueError):
            export_srt(source)

    def test_positive_sub_millisecond_interval_cannot_disappear_in_export(self):
        source = timeline()
        source['segments'][0].update(startMs=1000.1, endMs=1000.4)
        for export in (export_srt, export_lrc):
            with self.assertRaisesRegex(ValueError, '舍入'):
                export(source)

    def test_multiline_srt_is_safe_and_lrc_remains_one_line_per_entry(self):
        source = timeline()
        source['segments'][0]['text'] = '第一行\r\n\r\n第二行'
        srt = export_srt(source)
        self.assertIn('第一行\n第二行\n\n2\n', srt)
        self.assertNotIn('\r', srt)
        self.assertIn('[00:01.000]第一行 第二行\n', export_lrc(source))

    def test_utf8_atomic_file_export_and_invalid_format(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / '歌词输出.srt'
            result = write_subtitles(timeline(), target)
            self.assertEqual(result, target.resolve())
            self.assertEqual(target.read_bytes().decode('utf-8'), export_srt(timeline()))
            self.assertFalse(target.read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual(list(Path(temporary).glob('*.tmp')), [])
            with self.assertRaises(ValueError):
                write_subtitles(timeline(), target, format='video')

    def test_failed_atomic_replace_preserves_old_artifact_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'lyrics.lrc'
            target.write_text('previous valid artifact', encoding='utf-8')
            with patch('worker.subtitle_export.os.replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    write_subtitles(timeline(), target, format='lrc')
            self.assertEqual(target.read_text(), 'previous valid artifact')
            self.assertEqual(list(Path(temporary).glob('*.tmp')), [])

    def test_control_characters_and_invalid_word_intervals_are_refused(self):
        source = timeline()
        source['segments'][0]['text'] = '歌词\0危险'
        with self.assertRaises(ValueError):
            export_srt(source)
        source = timeline()
        source['quality'] = 'word'
        for row in source['segments']:
            row['words'] = [{'text': row['text'], 'startMs': row['startMs'], 'endMs': row['endMs']}]
        source['segments'][0]['words'][0]['endMs'] = 1700
        with self.assertRaises(ValueError):
            export_lrc(source)


if __name__ == '__main__':
    unittest.main()
