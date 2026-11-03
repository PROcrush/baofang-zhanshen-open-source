import json
import tempfile
import unittest
from pathlib import Path

from app.live_settings import line_starts, tap_offset
from app.setlist import MAX_ITEMS, Setlist


def timeline(quality='sentence'):
    words = [{'text': c, 'startMs': 2000 + i * 500, 'endMs': 2500 + i * 500} for i, c in enumerate('今晚的节奏')]
    return {'quality': quality, 'segments': [
        {'text': '今晚的节奏', 'startMs': 2000, 'endMs': 4500, 'words': words},
        {'text': '第二句', 'startMs': 8000, 'endMs': 10000, 'words': []},
        {'text': '第三句', 'startMs': 15000, 'endMs': 17000, 'words': []}]}


class TapOffsetTest(unittest.TestCase):
    def test_tap_late_singer_delays_lyrics(self):
        # 歌手在源位置 8300 才唱第二句：歌词要晚 300ms
        self.assertEqual(tap_offset(timeline(), 8300, 0), (300, 1))

    def test_tap_early_singer_advances_lyrics(self):
        self.assertEqual(tap_offset(timeline(), 7650, 0), (-350, 1))

    def test_uses_current_offset_to_find_line(self):
        # 已经延后 1000ms，打拍在 9100：屏幕上是 8100，对准第二句
        self.assertEqual(tap_offset(timeline(), 9100, 1000), (1100, 1))

    def test_rounds_to_ten_ms(self):
        self.assertEqual(tap_offset(timeline(), 8304, 0)[0], 300)
        self.assertEqual(tap_offset(timeline(), 8306, 0)[0], 310)

    def test_no_line_nearby(self):
        self.assertIsNone(tap_offset(timeline(), 12000, 0))
        self.assertIsNone(tap_offset(None, 8000, 0))
        self.assertIsNone(tap_offset({'quality': 'word', 'segments': []}, 8000, 0))

    def test_never_beyond_three_seconds(self):
        self.assertIsNone(tap_offset(timeline(), 11100, 2900))

    def test_does_not_change_timeline(self):
        data = timeline()
        before = json.dumps(data, sort_keys=True)
        tap_offset(data, 8300, 0, traditional=True)
        self.assertEqual(json.dumps(data, sort_keys=True), before)

    def test_traditional_uses_display_lines(self):
        self.assertEqual(line_starts(timeline('word'), traditional=False), [2000, 8000, 15000])
        self.assertEqual(line_starts(timeline('word'), traditional=True)[0], 2000)


def rows():
    return [{'versionId': 'sha256:a', 'title': '甲', 'offlineReady': True, 'durationText': '03:00'},
            {'versionId': 'sha256:b', 'title': '乙', 'offlineReady': True, 'needsReview': True},
            {'versionId': 'sha256:c', 'title': '丙', 'offlineReady': False}]


class SetlistTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'setlist.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_add_view_and_persist(self):
        setlist = Setlist(self.path)
        for version in ('sha256:a', 'sha256:b', 'sha256:c', 'sha256:gone'):
            self.assertTrue(setlist.add(version))
        self.assertFalse(setlist.add('sha256:a'))
        view = Setlist(self.path).view(rows())
        self.assertEqual([row['state'] for row in view['items']], ['ready', 'review', 'missing', 'gone'])
        self.assertEqual(view['counts'], {'ready': 1, 'review': 1, 'missing': 1, 'gone': 1})
        self.assertEqual(view['next']['versionId'], 'sha256:a')

    def test_move_remove_and_played(self):
        setlist = Setlist(self.path)
        for version in ('sha256:a', 'sha256:b', 'sha256:c'):
            setlist.add(version)
        self.assertTrue(setlist.move(2, -1))
        self.assertFalse(setlist.move(0, -1))
        self.assertEqual(setlist.items, ['sha256:a', 'sha256:c', 'sha256:b'])
        self.assertTrue(setlist.mark_played(['sha256:a', 'sha256:x']))
        self.assertFalse(setlist.mark_played(['sha256:a']))
        view = setlist.view(rows(), loaded={'sha256:c'})
        self.assertEqual(view['played'], 1)
        self.assertTrue(view['items'][1]['onDeck'])
        self.assertEqual(view['next']['versionId'], 'sha256:b')
        setlist.remove(0)
        self.assertEqual(setlist.played, set())
        setlist.set_played(0, True)
        setlist.clear(played_only=True)
        self.assertEqual(Setlist(self.path).items, ['sha256:b'])
        setlist.clear()
        self.assertEqual(Setlist(self.path).items, [])

    def test_bad_file_and_limits(self):
        self.path.write_text('{oops')
        setlist = Setlist(self.path)
        self.assertEqual(setlist.items, [])
        self.path.write_text(json.dumps({'schemaVersion': 1, 'items': ['x', 'x', 7, ''], 'played': ['x', 'y']}))
        setlist = Setlist(self.path)
        self.assertEqual(setlist.items, ['x'])
        self.assertEqual(setlist.played, {'x'})
        with self.assertRaises(ValueError):
            setlist.add('')
        with self.assertRaises(ValueError):
            setlist.remove(5)
        setlist.items = [f'v{i}' for i in range(MAX_ITEMS)]
        with self.assertRaises(ValueError):
            setlist.add('new')


if __name__ == '__main__':
    unittest.main()
