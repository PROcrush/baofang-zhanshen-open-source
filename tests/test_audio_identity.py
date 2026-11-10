import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app.storage import inspect_audio
from worker.index_audio import inspect_indexed_audio
from worker.studio_render import find_ffmpeg


class AudioIdentityTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def write_wav(self, name, period=16, rate=16000, channels=1):
        path = self.root / name
        samples = [9000 if sample % period < period // 2 else -9000 for sample in range(8000)]
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(channels)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(struct.pack('<8000h', *samples))
        return path

    def run_ffmpeg(self, *arguments):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.skipTest('FFmpeg is unavailable')
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', *arguments],
                       check=True, capture_output=True, timeout=15)

    def test_mp3_retagging_preserves_audio_identity_and_duration(self):
        source = self.root / 'song.mp3'
        retagged = self.root / 'retagged.mp3'
        self.run_ffmpeg('-i', str(self.write_wav('source.wav')), '-c:a', 'libmp3lame',
                        '-metadata', 'title=Before', str(source))
        self.run_ffmpeg('-i', str(source), '-map', '0:a', '-c:a', 'copy',
                        '-metadata', 'title=After editing metadata', str(retagged))

        original = inspect_indexed_audio(source)
        changed = inspect_indexed_audio(retagged)
        self.assertNotEqual(original['versionId'], changed['versionId'])
        self.assertNotEqual(original['size'], changed['size'])
        self.assertEqual(original['audioContentId'], changed['audioContentId'])
        self.assertIsNone(original['durationMs'])
        self.assertIsNone(changed['durationMs'])
        self.assertAlmostEqual(original['audioClockDurationMs'], 500, places=2)
        self.assertEqual(original['audioClockDurationMs'], changed['audioClockDurationMs'])

    def test_same_size_and_duration_do_not_hide_audio_differences(self):
        if not find_ffmpeg():
            self.skipTest('FFmpeg is unavailable')
        original = inspect_indexed_audio(self.write_wav('original.wav'))
        changed = inspect_indexed_audio(self.write_wav('different.wav', period=12))
        changed_layout = inspect_indexed_audio(self.write_wav('layout.wav', rate=8000, channels=2))
        self.assertEqual(original['size'], changed['size'])
        self.assertEqual(original['size'], changed_layout['size'])
        self.assertEqual(original['durationMs'], changed['durationMs'])
        self.assertEqual(original['durationMs'], changed_layout['durationMs'])
        self.assertNotEqual(original['audioContentId'], changed['audioContentId'])
        self.assertNotEqual(original['audioContentId'], changed_layout['audioContentId'])

    def test_unavailable_decoder_keeps_raw_file_proof(self):
        path = self.write_wav('fallback.wav')
        with patch('worker.index_audio.find_ffmpeg', return_value=None):
            self.assertEqual(inspect_indexed_audio(path), inspect_audio(path))

    def test_file_replaced_during_decode_is_rejected(self):
        path = self.write_wav('changed.wav')

        def replace(_):
            self.write_wav('replacement.wav', period=12).replace(path)
            return None

        with patch('worker.index_audio.decoded_audio_proof', side_effect=replace):
            with self.assertRaisesRegex(ValueError, '发生变化'):
                inspect_indexed_audio(path)
