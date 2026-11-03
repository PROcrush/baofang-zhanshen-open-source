import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from app.duet import duet_view, parse_singer, singer_at
from app.duplicates import mark_duplicates, title_key
from app.guest_inbox import MAX_TEXT, GuestInbox, clean_text, write_qr
from app.midi_control import MidiControl, describe, message_key, validate_mapping
from app.showlog import ShowLog


class DuetTest(unittest.TestCase):
    def test_parse_tags(self):
        self.assertEqual(parse_singer('男：你好'), ('male', '你好'))
        self.assertEqual(parse_singer('(女)我在'), ('female', '我在'))
        self.assertEqual(parse_singer('【合】一起唱'), ('chorus', '一起唱'))
        self.assertEqual(parse_singer('M: hello'), ('male', 'hello'))
        self.assertEqual(parse_singer('男人哭吧'), (None, '男人哭吧'))
        self.assertEqual(parse_singer('男：'), (None, '男：'))

    def test_view_carries_singer_and_keeps_source(self):
        words = [{'text': '女', 'startMs': 0, 'endMs': 100}, {'text': '：', 'startMs': 100, 'endMs': 200},
                 {'text': '我', 'startMs': 200, 'endMs': 500}]
        timeline = {'quality': 'word', 'segments': [
            {'text': '男：第一句', 'startMs': 0, 'endMs': 1000, 'words': []},
            {'text': '还是男声', 'startMs': 1000, 'endMs': 2000, 'words': []},
            {'text': '女：我', 'startMs': 2000, 'endMs': 3000, 'words': words}]}
        before = json.dumps(timeline, sort_keys=True)
        view = duet_view(timeline)
        self.assertEqual(json.dumps(timeline, sort_keys=True), before)
        self.assertEqual([r['singer'] for r in view['segments']], ['male', 'male', 'female'])
        self.assertEqual([r['text'] for r in view['segments']], ['第一句', '还是男声', '我'])
        self.assertEqual(view['segments'][2]['words'], words[2:])
        self.assertEqual(view['segments'][2]['startMs'], 2000)
        self.assertIs(duet_view(timeline), view)
        self.assertEqual(singer_at(view, 500), 'male')
        self.assertEqual(singer_at(view, 2500), 'female')
        self.assertIsNone(singer_at(view, 9000))

    def test_shared_token_keeps_tag(self):
        timeline = {'quality': 'word', 'segments': [
            {'text': '男：好', 'startMs': 0, 'endMs': 900, 'words': [{'text': '男：好', 'startMs': 0, 'endMs': 900}]}]}
        row = duet_view(timeline)['segments'][0]
        self.assertEqual((row['text'], row['singer']), ('男：好', 'male'))


class ShowLogTest(unittest.TestCase):
    def test_add_read_summary_export(self):
        with tempfile.TemporaryDirectory() as folder:
            log = ShowLog(Path(folder) / 'log', clock=lambda: 1_790_000_000)
            log.add('song', '夜色开场', deck='A')
            log.add('hide')
            log.add('tap', '250 ms')
            with self.assertRaises(ValueError):
                log.add('bogus')
            day = log.days()[0]
            rows = log.read(day)
            self.assertEqual([r['label'] for r in rows], ['上屏歌曲', '隐藏歌词', '打拍对齐'])
            self.assertEqual(log.summary(rows)['songs'], 1)
            with open(log.path_for(day), 'a') as handle:
                handle.write('not json\n')
            self.assertEqual(len(log.read(day)), 3)
            target = Path(folder) / 'out.csv'
            self.assertEqual(log.export_csv(day, target), 3)
            self.assertIn('夜色开场', target.read_text(encoding='utf-8-sig'))


class GuestInboxTest(unittest.TestCase):
    def test_accept_rules(self):
        now = [100.0]
        inbox = GuestInbox(clock=lambda: now[0])
        self.assertEqual(inbox.accept('1.1.1.1', {'text': '  '})[0], 400)
        self.assertEqual(inbox.accept('1.1.1.1', 'x')[0], 400)
        self.assertEqual(inbox.accept('1.1.1.1', {'text': '生日快乐' * 20, 'name': '小王\x00同学很长很长的名字'})[0], 200)
        self.assertEqual(inbox.accept('1.1.1.1', {'text': '再来'})[0], 429)
        now[0] += 21
        self.assertEqual(inbox.accept('1.1.1.1', {'text': '再来'})[0], 200)
        rows = inbox.drain()
        self.assertEqual(len(rows[0]['text']), MAX_TEXT)
        self.assertEqual(rows[0]['name'], '小王同学很长很长')
        self.assertEqual(inbox.drain(), [])
        self.assertEqual(clean_text('a\nb\tc', 10), 'abc')

    def test_server_round_trip_on_loopback(self):
        inbox = GuestInbox()
        url = inbox.start(host='127.0.0.1')
        try:
            page = urllib.request.urlopen(url, timeout=3).read().decode('utf-8')
            self.assertIn('给包房送一句话', page)
            request = urllib.request.Request(url, data=json.dumps({'text': '干杯'}).encode(),
                                             headers={'Content-Type': 'application/json'}, method='POST')
            self.assertEqual(urllib.request.urlopen(request, timeout=3).status, 200)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(url.rsplit('/', 1)[0] + '/wrong', timeout=3)
            self.assertEqual(caught.exception.code, 404)
            big = urllib.request.Request(url, data=b'x' * 5000, method='POST')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(big, timeout=3)
            self.assertEqual(caught.exception.code, 413)
            self.assertEqual([r['text'] for r in inbox.drain()], ['干杯'])
            with tempfile.TemporaryDirectory() as folder:
                image = write_qr(url, Path(folder) / 'qr.png')
                self.assertTrue(image.read_bytes().startswith(b'\x89PNG'))
        finally:
            inbox.stop()
        self.assertFalse(inbox.running)


class MidiTest(unittest.TestCase):
    def test_keys_learn_and_persist(self):
        note = SimpleNamespace(type='note_on', channel=0, note=36, velocity=100)
        release = SimpleNamespace(type='note_on', channel=0, note=36, velocity=0)
        knob = SimpleNamespace(type='control_change', channel=1, control=7, value=64)
        self.assertEqual(message_key(note), 'note:0:36')
        self.assertIsNone(message_key(release))
        self.assertEqual(message_key(knob), 'cc:1:7')
        self.assertEqual(describe('note:0:36'), '音符 36 · 通道 1')
        with self.assertRaises(ValueError):
            validate_mapping({'tap': 'bad'})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'midi.json'
            midi = MidiControl(path)
            midi.learn('tap')
            midi._callback(note)
            self.assertEqual(midi.poll(), [])
            self.assertEqual(midi.mapping, {'tap': 'note:0:36'})
            midi.learn('toggleLyrics')
            midi._callback(note)  # same key moves to the new action
            midi.poll()
            self.assertEqual(midi.mapping, {'toggleLyrics': 'note:0:36'})
            midi._callback(note)
            midi._callback(release)
            self.assertEqual(midi.poll(), ['toggleLyrics'])
            self.assertEqual(MidiControl(path).mapping, {'toggleLyrics': 'note:0:36'})
            midi.clear('toggleLyrics')
            self.assertEqual(MidiControl(path).mapping, {})


class DuplicateTest(unittest.TestCase):
    def test_marks_lookalike_copies_only(self):
        rows = [{'versionId': 'a', 'title': '流星雨 (1)', 'durationMs': 200000},
                {'versionId': 'b', 'title': '流星雨', 'durationMs': 200600},
                {'versionId': 'c', 'title': '流星雨', 'durationMs': 260000},
                {'versionId': 'd', 'title': '别的歌', 'durationMs': 200000}]
        self.assertEqual(mark_duplicates(rows), 1)
        self.assertEqual([r['duplicateCount'] for r in rows], [1, 1, 0, 0])
        self.assertEqual(title_key('流星雨 - 副本'), title_key('流星雨'))


if __name__ == '__main__':
    unittest.main()
