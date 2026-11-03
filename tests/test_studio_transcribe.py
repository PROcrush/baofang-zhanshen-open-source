import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from worker.studio_transcribe import (SAMPLE_RATE, TranscriptionCancelled, convert_window,
                                     transcribe_audio, validate_request)


class Samples:
    def __init__(self, seconds):
        self.count = int(seconds * SAMPLE_RATE)

    def __len__(self):
        return self.count


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.audio = self.root / 'original.wav'
        self.audio.write_bytes(b'original source audio fixture')
        self.output = self.root / 'recognized.json'
        self.request = {'audioPath': str(self.audio), 'durationMs': 4000,
                        'model': 'tiny', 'output': str(self.output)}

    def run_asr(self, request=None, backend=None, **options):
        request = request or self.request
        return transcribe_audio(request, backend=backend or (lambda samples, language: {'segments': []}),
                                decoder=options.pop('decoder', lambda path, offset, length: Samples(length)),
                                probe=lambda path: {'format': {'duration': request['durationMs'] / 1000}},
                                **options)

    def test_real_model_word_ranges_are_not_split_into_characters(self):
        backend_result = {'segments': [{'start': 0, 'end': 3, 'text': ' 你好世界。 ', 'words': [
            {'start': .2, 'end': 1.2, 'word': ' 你好'},
            {'start': 1.5, 'end': 2.7, 'word': '世界。 '}]}]}
        done = self.run_asr(backend=lambda samples, language: backend_result)
        data = json.loads(self.output.read_text())
        self.assertEqual(data['segments'][0]['characters'], [
            {'start': .2, 'end': 1.2, 'text': '你好'}, {'start': 1.5, 'end': 2.7, 'text': '世界。'}])
        self.assertTrue(done['receipt']['actualWordTiming'])
        self.assertTrue(done['receipt']['allSegmentsWordTimed'])
        self.assertFalse(done['receipt']['characterTimingSynthesized'])
        self.assertEqual(done['receipt']['sourceSHA256'], hashlib.sha256(self.audio.read_bytes()).hexdigest())

    def test_invalid_or_missing_word_spans_fall_back_to_sentence(self):
        rows = [{'start': 0, 'end': 1, 'text': '你好',
                 'words': [{'word': '你', 'start': 0, 'end': 0}, {'word': '好', 'start': .1, 'end': .5}]},
                {'start': 1, 'end': 2, 'text': '世界', 'words': []},
                {'start': 2, 'end': 3, 'text': '修改的词',
                 'words': [{'word': '不同的词', 'start': 2, 'end': 3}]}]
        done = self.run_asr(backend=lambda samples, language: {'segments': rows})
        data = json.loads(self.output.read_text())
        self.assertTrue(all('characters' not in row for row in data['segments']))
        self.assertFalse(done['receipt']['actualWordTiming'])
        self.assertEqual(done['receipt']['discardedInvalidWordTimingSegments'], 2)

    def test_window_offset_and_bounded_decode(self):
        request = {**self.request, 'durationMs': 65000}
        offsets = []
        def decoder(path, offset, length):
            offsets.append((offset, length))
            return Samples(length)
        def backend(samples, language):
            self.assertLessEqual(len(samples), 30 * SAMPLE_RATE)
            return {'segments': [{'start': .1, 'end': 1, 'text': '歌词',
                                  'words': [{'start': .1, 'end': 1, 'word': '歌词'}]}]}
        self.run_asr(request, backend, decoder=decoder)
        rows = json.loads(self.output.read_text())['segments']
        self.assertEqual(offsets, [(0, 30), (30, 30), (60, 5)])
        self.assertEqual([row['start'] for row in rows], [.1, 30.1, 60.1])
        self.assertEqual(rows[-1]['characters'][0]['end'], 61)

    def test_invalid_requests_and_existing_destination(self):
        for changes in ({'durationMs': float('nan')}, {'durationMs': True}, {'durationMs': 0},
                        {'durationMs': 10 ** 400}, {'audioPath': 'relative.wav'},
                        {'model': 'large'}, {'modelPath': '/Applications/Test.app/model.pt'},
                        {'language': '../zh'}, {'extra': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_request({**self.request, **changes})
        self.output.write_text('keep me')
        with self.assertRaises(ValueError):
            self.run_asr()
        self.assertEqual(self.output.read_text(), 'keep me')

    def test_cancel_before_and_after_inference_publishes_nothing(self):
        with self.assertRaises(TranscriptionCancelled):
            self.run_asr(cancelled=lambda: True)
        flag = [False]
        def backend(samples, language):
            flag[0] = True
            return {'segments': [{'start': 0, 'end': 1, 'text': '取消'}]}
        with self.assertRaises(TranscriptionCancelled):
            self.run_asr(backend=backend, cancelled=lambda: flag[0])
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.baofang-asr-*')))

    def test_source_change_and_racing_output_do_not_publish_false_success(self):
        def backend(samples, language):
            self.audio.write_bytes(b'changed outside recognition')
            return {'segments': []}
        with self.assertRaisesRegex(ValueError, '原音频发生变化'):
            self.run_asr(backend=backend)
        self.assertFalse(self.output.exists())
        def race(samples, language):
            self.output.write_text('external racing output')
            return {'segments': []}
        with self.assertRaises(FileExistsError):
            self.run_asr(backend=race)
        self.assertEqual(self.output.read_text(), 'external racing output')
        self.assertFalse(list(self.root.glob('.baofang-asr-*')))

    def test_duration_mismatch_and_bounded_decoder_refused(self):
        with self.assertRaisesRegex(ValueError, '实际音频不一致'):
            transcribe_audio(self.request, backend=lambda audio, language: {'segments': []},
                             probe=lambda path: {'format': {'duration': 10}})
        for samples in (Samples(0), Samples(31), Samples(2)):
            with self.subTest(samples=len(samples)), self.assertRaises(ValueError):
                self.run_asr(decoder=lambda path, offset, length: samples)

    def test_invalid_model_timestamps_never_make_success(self):
        for result in ({'segments': [{'start': float('nan'), 'end': 1, 'text': '错'}]},
                       {'segments': [{'start': 2, 'end': 1, 'text': '倒置'}]},
                       {'segments': [{'start': 0, 'end': 2, 'text': '一'}, {'start': 1, 'end': 3, 'text': '二'}]},
                       {'segments': [{'start': 0, 'end': 1, 'text': '错', 'words': 'bad'}]}):
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.run_asr(backend=lambda samples, language: result)
            self.assertFalse(self.output.exists())

    def test_progress_done_and_original_preserved(self):
        before = self.audio.read_bytes()
        events = []
        done = self.run_asr(event=events.append)
        self.assertEqual(events[-1], done)
        self.assertEqual(done['timelinePath'], str(self.output.resolve()))
        self.assertTrue(all(0 <= row['value'] <= 1 for row in events if row['type'] == 'progress'))
        self.assertEqual(before, self.audio.read_bytes())
        self.assertTrue(done['receipt']['manualReviewRequired'])
        self.assertFalse(done['receipt']['networkUsedDuringRecognition'])

    def test_window_trim_drops_unusable_word_clock_without_subdivision(self):
        rows, count, discarded = convert_window({'segments': [{'start': 0, 'end': 5, 'text': '末尾',
            'words': [{'word': '末尾', 'start': 1, 'end': 5}]}]}, 30, 4)
        self.assertEqual(rows[0]['end'], 34)
        self.assertNotIn('characters', rows[0])
        self.assertEqual((count, discarded), (0, 1))

    def test_cancelled_delivery_retracts_only_our_published_result(self):
        def event(value):
            if value['type'] == 'done':
                raise TranscriptionCancelled('cancel at publication boundary')
        with self.assertRaises(TranscriptionCancelled):
            self.run_asr(event=event)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.baofang-asr-*')))


if __name__ == '__main__':
    unittest.main()
