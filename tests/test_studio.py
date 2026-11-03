import copy
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if sys.platform == 'darwin':
    from scripts.repair_qt_metadata import repair_qt_metadata
    repair_qt_metadata()

from PySide6.QtWidgets import QApplication

from app.studio import (PROJECT_KIND, PROJECT_VERSION, StudioController,
                        check_identity, file_identity, from_recognition, to_recognition, validate_segments)
from worker.studio_render import EFFECTS, ENGINE_ID, TEMPLATES, draw_frame, find_ffmpeg, probe_media


ROOT = Path(__file__).resolve().parent.parent
APPLICATION = QApplication.instance() or QApplication([])


def wait_idle(controller, timeout=90):
    deadline = time.monotonic() + timeout
    while controller.state['busy'] and time.monotonic() < deadline:
        APPLICATION.processEvents()
        time.sleep(0.01)
    APPLICATION.processEvents()
    if controller.state['busy']:
        controller.cancelJob()
        raise AssertionError('Studio job timed out: ' + controller.state['status'])


def synthesize_audio(path):
    rate, frames = 48000, 38400
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b''.join(struct.pack('<h', int(1600 * math.sin(2 * math.pi * 220 * n / rate)))
                                  for n in range(frames)))


class StudioDataTests(unittest.TestCase):
    @unittest.skipUnless(find_ffmpeg(), 'A general FFmpeg executable is not installed')
    def test_video_metadata_includes_silent_video_without_requiring_audio_or_full_decode(self):
        with tempfile.TemporaryDirectory() as folder:
            movie = Path(folder) / 'silent-video.mp4'
            subprocess.run([find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-n', '-f', 'lavfi',
                            '-i', 'color=c=blue:s=160x90:r=24:d=0.25', '-an', '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p', str(movie)], check=True, timeout=30)
            data = probe_media(movie, include_video=True)
            video = [row for row in data['streams'] if row.get('codec_type') == 'video']
            self.assertEqual((video[0]['width'], video[0]['height']), (160, 90))
            self.assertFalse(any(row.get('codec_type') == 'audio' for row in data['streams']))
            self.assertGreater(float(data['format']['duration']), 0)
            with self.assertRaises(ValueError):
                probe_media(movie, verify_video=True)

    def test_engine_seconds_roundtrip_keeps_observed_character_intervals(self):
        native = {'segments': [{'start': 0.1, 'end': 0.7, 'text': '歌词', 'characters': [
            {'text': '歌', 'start': 0.1, 'end': 0.3}, {'text': '词', 'start': 0.4, 'end': 0.7}]}]}
        self.assertEqual(to_recognition(from_recognition(native, 800)), native)

    def test_invalid_or_out_of_range_timestamps_are_rejected(self):
        for start, end in [(float('nan'), 600), (100, float('inf')), (-1, 600), (700, 600), (0, 1000)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                validate_segments([{'startMs': start, 'endMs': end, 'text': '歌词'}], 800)

    def test_identity_detects_changed_bytes_even_when_stat_is_restored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.txt'
            path.write_text('abcd')
            identity = file_identity(path)
            path.write_text('efgh')
            os.utime(path, ns=(path.stat().st_atime_ns, identity['mtimeNs']))
            with self.assertRaises(ValueError):
                check_identity(identity)


class StudioControllerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name).resolve()
        self.controller = StudioController(ROOT, self.directory / 'runtime')
        self.audio = self.directory / 'synthetic.wav'
        synthesize_audio(self.audio)

    def tearDown(self):
        self.controller.shutdown()
        APPLICATION.processEvents()
        self.folder.cleanup()

    def populate_project(self):
        self.controller._sources['audio'] = file_identity(self.audio)
        self.controller._state.update(durationMs=800, segments=from_recognition({'segments': [
            {'start': 0.1, 'end': 0.7, 'text': '往事随疯', 'characters': [
                {'text': char, 'start': 0.1 + i * 0.15, 'end': 0.25 + i * 0.15}
                for i, char in enumerate('往事随疯')]}]}, 800))
        self.controller._sync_source_state()
        self.controller._emit()

    def test_default_state_has_no_user_media_and_only_advertises_implemented_independent_styles(self):
        state = self.controller.state
        self.assertEqual(state['audioPath'], '')
        self.assertEqual(state['segments'], [])
        self.assertFalse(state['dirty'])
        self.assertFalse(state['timelineSilent'])
        self.assertFalse(state['playbackPlaying'])
        self.assertEqual(state['templates'], TEMPLATES)
        self.assertEqual(state['effects'], EFFECTS)
        self.assertEqual(state['capabilities'], {'lyricTemplates': 16, 'captionEffects': 8,
            'jizuraLayouts': 184, 'transitions': 53, 'fonts': 0, 'gradients': 1})
        self.assertIn('包房战神自研引擎', state['engineLabel'])
        self.assertFalse(hasattr(self.controller, 'openOriginal'))

    def test_edit_preserves_character_times_and_length_change_downgrades_then_undo_redo(self):
        self.populate_project()
        before = copy.deepcopy(self.controller.state['segments'][0])
        self.controller.updateSegment(0, '往事随风')
        changed = self.controller.state['segments'][0]
        self.assertEqual([(c['startMs'], c['endMs']) for c in changed['characters']],
                         [(c['startMs'], c['endMs']) for c in before['characters']])
        self.assertEqual(changed['characters'][-1]['text'], '风')
        self.controller.updateSegment(0, '往事随风去')
        self.assertNotIn('characters', self.controller.state['segments'][0])
        self.assertEqual(self.controller.state['segments'][0]['startMs'], before['startMs'])
        self.controller.undo()
        self.assertEqual(self.controller.state['segments'][0], changed)
        self.controller.redo()
        self.assertEqual(self.controller.state['segments'][0]['text'], '往事随风去')

    def test_save_reopen_validates_source_identity_and_strict_project_version(self):
        self.populate_project()
        project = self.directory / 'track.bfstudio.json'
        self.controller.saveProject(str(project))
        self.assertTrue(project.exists(), self.controller.state['status'])
        self.assertFalse(self.controller.state['dirty'])
        self.controller.updateSegment(0, '往事随风')
        self.controller.openProject(str(project))
        self.assertEqual(self.controller.state['segments'][0]['text'], '往事随疯')
        self.assertFalse(self.controller.state['dirty'])
        data = json.loads(project.read_text())
        self.assertEqual(data['kind'], PROJECT_KIND)
        self.assertEqual(data['engineId'], ENGINE_ID)
        self.assertNotIn('engineSha256', data)
        data['version'] = PROJECT_VERSION + 1
        project.write_text(json.dumps(data))
        self.controller.openProject(str(project))
        self.assertIn('版本不兼容', self.controller.state['status'])

    def test_project_cannot_overwrite_existing_foreign_json_or_source(self):
        self.populate_project()
        path = self.directory / 'existing.json'
        path.write_text('{"user":"data"}')
        self.controller.saveProject(str(path))
        self.assertEqual(path.read_text(), '{"user":"data"}')
        before = self.audio.read_bytes()
        self.controller.saveProject(str(self.audio))
        self.assertEqual(self.audio.read_bytes(), before)

    def test_open_project_keeps_unsaved_edits_in_reopenable_recovery_without_rewriting_original(self):
        self.populate_project()
        project = self.directory / 'track.bfstudio.json'
        self.controller.saveProject(str(project))
        original_bytes = project.read_bytes()
        self.controller.updateSegment(0, '往事随风去')
        unsaved = self.controller._snapshot()
        self.controller.openProject(str(project))
        recovery = Path(self.controller.state['recoveryPath'])
        self.assertTrue(recovery.is_file(), self.controller.state['status'])
        self.assertTrue(recovery.is_relative_to(self.controller.runtime / 'studio-recovery'))
        recovered = json.loads(recovery.read_text())
        self.assertEqual(recovered['sources'], unsaved['sources'])
        self.assertEqual(recovered['segments'], unsaved['segments'])
        self.assertEqual(project.read_bytes(), original_bytes)
        self.assertIn('恢复副本', self.controller.state['status'])
        self.assertFalse(self.controller.state['dirty'])
        self.controller.openProject(str(recovery))
        self.assertEqual(self.controller.state['segments'], unsaved['segments'])

    def test_recovery_write_failure_preserves_dirty_project_and_undo_stack(self):
        self.populate_project()
        project = self.directory / 'track.bfstudio.json'
        self.controller.saveProject(str(project))
        self.controller.updateSegment(0, '往事随风去')
        unsaved, undo = self.controller._snapshot(), copy.deepcopy(self.controller._undo)
        (self.controller.runtime / 'studio-recovery').write_text('blocked-directory')
        self.controller.openProject(str(project))
        self.assertEqual(self.controller._snapshot(), unsaved)
        self.assertEqual(self.controller._undo, undo)
        self.assertTrue(self.controller.state['dirty'])
        self.assertEqual(self.controller.state['recoveryPath'], '')
        self.assertIn('当前工程保持原样', self.controller.state['status'])

    def test_invalid_new_project_does_not_create_recovery_or_discard_edits(self):
        self.populate_project()
        self.controller.updateSegment(0, '往事随风去')
        unsaved = self.controller._snapshot()
        project = self.directory / 'invalid.bfstudio.json'
        project.write_text(json.dumps({'kind': PROJECT_KIND, 'version': PROJECT_VERSION + 1}))
        self.controller.openProject(str(project))
        self.assertEqual(self.controller._snapshot(), unsaved)
        self.assertFalse((self.controller.runtime / 'studio-recovery').exists())
        self.assertTrue(self.controller.state['canUndo'])

    def test_recovery_also_restores_settings_changed_before_first_audio_import(self):
        self.populate_project()
        project = self.directory / 'track.bfstudio.json'
        self.controller.saveProject(str(project))
        blank = StudioController(ROOT, self.directory / 'blank-runtime')
        self.addCleanup(blank.shutdown)
        blank.setSetting('fontSize', 80)
        blank.openProject(str(project))
        recovery = blank.state['recoveryPath']
        self.assertTrue(recovery, blank.state['status'])
        blank.openProject(recovery)
        self.assertEqual(blank.state['settings']['fontSize'], 80)
        self.assertEqual(blank.state['audioPath'], '')
        self.assertEqual(blank.state['durationMs'], 0)

    def test_seek_is_silent_and_settings_reject_unknown_catalog_ids(self):
        self.populate_project()
        self.controller.seek(150)
        self.assertEqual(self.controller.state['currentText'], '往事随疯')
        self.controller.seek(-5)
        self.assertEqual(self.controller.state['positionMs'], 0)
        self.assertEqual(self.controller.state['nextText'], '往事随疯')
        before = self.controller.state['settings']['templateId']
        self.controller.setSetting('templateId', 'invented')
        self.assertEqual(self.controller.state['settings']['templateId'], before)
        self.controller.setSetting('width', 1280)
        self.assertEqual(self.controller.state['settings']['height'], 720)

    def test_background_video_has_no_image_url(self):
        path = self.directory / 'synthetic.mp4'
        path.write_bytes(b'fixture')
        self.controller.chooseBackground(str(path))
        self.assertFalse(self.controller.state['backgroundIsImage'])
        self.assertEqual(self.controller.state['backgroundUrl'], '')

    def test_changed_audio_prevents_project_open_and_export(self):
        self.populate_project()
        project = self.directory / 'track.bfstudio.json'
        self.controller.saveProject(str(project))
        self.audio.write_bytes(b'changed')
        self.controller.openProject(str(project))
        self.assertIn('源文件已变化', self.controller.state['status'])
        self.controller.exportVideo()
        self.assertFalse(self.controller.state['busy'])
        self.assertEqual(self.controller.state['resultPath'], '')

    def test_cancel_stops_private_process_and_restores_idle_state(self):
        self.assertTrue(self.controller._start_job('取消测试'))
        self.controller._launch(sys.executable, ['-c', 'import time; time.sleep(30)'], lambda _: None)
        deadline = time.monotonic() + 3
        while not self.controller._process.processId() and time.monotonic() < deadline:
            APPLICATION.processEvents()
        self.controller.cancelJob()
        wait_idle(self.controller, timeout=5)
        self.assertIn('任务已取消', self.controller.state['status'])
        self.assertIsNone(self.controller._process)

    @unittest.skipUnless(find_ffmpeg(), 'A general FFmpeg executable is not installed')
    def test_real_render_does_not_publish_result_if_source_changes_during_final_verification(self):
        self.populate_project()
        self.controller.setSetting('templateId', 'headline')
        original_probe = self.controller._probe

        def change_source_before_final_probe(path, complete, **options):
            self.audio.write_bytes(b'externally changed source')
            original_probe(path, complete, **options)

        self.controller._probe = change_source_before_final_probe
        self.controller.exportVideo()
        wait_idle(self.controller, timeout=120)
        self.assertFalse(self.controller.state['busy'])
        self.assertEqual(self.controller.state['resultPath'], '')
        self.assertEqual(self.controller.state['resultUrl'], '')
        self.assertIn('渲染期间源文件发生变化', self.controller.state['status'])
        self.assertTrue(list(self.controller.jobs_root.glob('*/export.mp4')))

    def test_all_independent_templates_and_effects_paint_real_cpu_pixels(self):
        self.populate_project()
        settings = self.controller.state['settings']
        for template in TEMPLATES:
            for effect in EFFECTS:
                with self.subTest(template=template['id'], effect=effect['id']):
                    settings.update(templateId=template['id'], effectId=effect['id'])
                    frame = draw_frame(self.controller.state['segments'], settings, 350)
                    blank = draw_frame(self.controller.state['segments'], settings, 0)
                    self.assertEqual((frame.width(), frame.height()), (854, 480))
                    pixels = frame.bits().tobytes()
                    self.assertTrue(pixels != blank.bits().tobytes(), '歌词没有出现在渲染画面中')
                    fill = bytes.fromhex(settings['textColor'][1:]) + b'\xff'
                    self.assertTrue(fill in pixels, '渲染画面缺少所设歌词颜色')

    @unittest.skipUnless(find_ffmpeg(), 'A general FFmpeg executable is not installed')
    def test_cpu_export_uses_independent_image_and_video_background_without_changing_sources(self):
        self.populate_project()
        picture = self.directory / 'generated-background.png'
        draw_frame([], self.controller.state['settings'], 0).save(str(picture))
        image_identity = file_identity(picture)
        self.controller.chooseBackground(str(picture))
        self.assertTrue(self.controller.state['backgroundIsImage'])
        self.controller.renderPreview()
        wait_idle(self.controller)
        movie = Path(self.controller.state['resultPath'])
        self.assertTrue(movie.is_file(), self.controller.state['status'])
        movie_identity = file_identity(movie)
        self.controller.chooseBackground(str(movie))
        self.assertEqual(self.controller.state['backgroundUrl'], '')
        self.controller.exportVideo()
        wait_idle(self.controller)
        self.assertTrue(self.controller.state['resultPath'], self.controller.state['status'])
        self.assertNotEqual(self.controller.state['resultPath'], str(movie))
        self.assertEqual(file_identity(picture), image_identity)
        self.assertEqual(file_identity(movie), movie_identity)

    @unittest.skipUnless(find_ffmpeg(), 'A general FFmpeg executable is not installed')
    def test_independent_import_correction_edit_project_roundtrip_and_cpu_video_export(self):
        source_identity = file_identity(self.audio)
        self.controller.chooseAudio(str(self.audio))
        wait_idle(self.controller)
        self.assertEqual(self.controller.state['audioPath'], str(self.audio), self.controller.state['status'])
        self.assertAlmostEqual(self.controller.state['durationMs'], 800, delta=2)
        srt = self.directory / 'fixture.srt'
        srt.write_text('1\n00:00:00,050 --> 00:00:00,750\n离线字幕测试\n', encoding='utf-8')
        self.controller.chooseSubtitles(str(srt))
        wait_idle(self.controller)
        self.assertEqual(len(self.controller.state['segments']), 1, self.controller.state['status'])
        original = self.controller.state['segments'][0]['text']
        reference = self.directory / 'reference.txt'
        reference.write_text(original, encoding='utf-8')
        self.controller.chooseReference(str(reference))
        wait_idle(self.controller)
        self.assertEqual(self.controller.state['referencePath'], str(reference), self.controller.state['status'])
        self.assertEqual(self.controller.state['segments'][0]['text'], original)
        self.controller.updateSegment(0, '真实引擎导出')
        self.controller.undo()
        self.assertEqual(self.controller.state['segments'][0]['text'], original)
        project = self.directory / 'real.bfstudio.json'
        self.controller.saveProject(str(project))
        self.controller.openProject(str(project))
        self.assertFalse(self.controller.state['dirty'])
        self.controller.setSetting('templateId', 'headline')
        self.controller.exportVideo()
        wait_idle(self.controller, timeout=120)
        result = self.controller.state['resultPath']
        self.assertTrue(result, self.controller.state['status'])
        self.assertTrue(Path(result).is_file())
        self.assertTrue(Path(result).is_relative_to(self.controller.jobs_root))
        self.assertEqual(self.controller.state['previewMode'], 'rendered')
        self.assertTrue((Path(result).parent / 'preview-frame.png').is_file())
        request = json.loads((Path(result).parent / 'render-request.json').read_text())
        self.assertEqual(request['engineId'], ENGINE_ID)
        ffmpeg = Path(find_ffmpeg())
        self.assertFalse(any(part.endswith('.app') for part in ffmpeg.parts))
        self.assertEqual(file_identity(self.audio), source_identity)


if __name__ == '__main__':
    unittest.main()
