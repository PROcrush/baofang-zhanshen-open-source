import json
import hashlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.studio import StudioController
from app.studio_projects import RecentProjects
from tests.test_studio import APPLICATION, ROOT, synthesize_audio, wait_idle


class StudioUsabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name).resolve()
        self.studio = StudioController(ROOT, self.folder / 'runtime')
        self.audio = self.folder / '演出版本 #1.wav'
        synthesize_audio(self.audio)
        self.studio.chooseAudio(str(self.audio)); wait_idle(self.studio)
        subtitles = self.folder / '歌词.srt'
        subtitles.write_text('1\n00:00:00,100 --> 00:00:00,700\n把喜欢的声音留下\n')
        self.studio.chooseSubtitles(str(subtitles)); wait_idle(self.studio)

    def tearDown(self):
        self.studio.shutdown(); APPLICATION.processEvents(); self.temporary.cleanup()

    def test_saved_project_saves_in_place_and_save_as_is_explicit(self):
        original = self.folder / '排练.bfstudio.json'
        self.studio.saveProject(str(original))
        self.studio.updateSegment(0, '把今晚的声音留下')
        with patch('app.studio.QFileDialog.getSaveFileName') as dialog:
            self.studio.saveProject()
            dialog.assert_not_called()
        self.assertFalse(self.studio.state['dirty'])
        self.assertEqual(json.loads(original.read_text())['segments'][0]['text'], '把今晚的声音留下')
        copy = self.folder / '正式演出.bfstudio.json'
        with patch('app.studio.QFileDialog.getSaveFileName', return_value=(str(copy), '')) as dialog:
            self.studio.saveProjectAs()
            dialog.assert_called_once()
        self.assertEqual(copy.read_bytes(), original.read_bytes())
        self.assertEqual(RecentProjects(self.folder / 'runtime').paths, [str(copy), str(original)])

    def test_quick_save_does_not_overwrite_an_external_edit(self):
        project = self.folder / '歌曲.bfstudio.json'
        self.studio.saveProject(str(project))
        data = json.loads(project.read_text()); data['settings']['fontSize'] = 50
        project.write_text(json.dumps(data)); external = project.read_bytes()
        self.studio.updateSegment(0, '把今晚的声音留下')
        self.studio.saveProject()
        self.assertEqual(project.read_bytes(), external)
        self.assertTrue(self.studio.state['dirty'])
        self.assertIn('工程文件已变化', self.studio.state['status'])

    def test_next_song_has_a_new_save_target_and_preserves_unsaved_previous_song(self):
        original = self.folder / '第一首.bfstudio.json'
        self.studio.saveProject(str(original))
        saved = original.read_bytes()
        original_id = json.loads(saved)['projectId']
        self.studio.updateSegment(0, '上一首尚未保存的修改')
        other = self.folder / '下一首.wav'; synthesize_audio(other)
        self.studio.chooseAudio(str(other)); wait_idle(self.studio)
        state = self.studio.state
        self.assertEqual(state['projectPath'], '')
        self.assertNotEqual(self.studio._project_id, original_id)
        self.assertFalse(state['segments'])
        self.assertFalse(state['canUndo'])
        recovery = Path(state['recoveryPath'])
        self.assertEqual(json.loads(recovery.read_text())['segments'][0]['text'], '上一首尚未保存的修改')
        with patch('app.studio.QFileDialog.getSaveFileName', return_value=('', '')) as dialog:
            self.studio.saveProject(); dialog.assert_called_once()
        self.assertEqual(original.read_bytes(), saved)
        new_project = self.folder / '第二首.bfstudio.json'
        self.studio.saveProject(str(new_project))
        self.assertEqual(Path(json.loads(new_project.read_text())['sources']['audio']['path']), other)
        self.assertEqual(original.read_bytes(), saved)
        self.studio.openProject(str(recovery))
        self.assertEqual(self.studio.state['segments'][0]['text'], '上一首尚未保存的修改')

    def test_next_song_does_not_replace_current_work_when_recovery_fails(self):
        original = self.folder / '第一首.bfstudio.json'
        self.studio.saveProject(str(original))
        self.studio.updateSegment(0, '仍需保留的修改')
        before, undo = self.studio._snapshot(), list(self.studio._undo)
        other = self.folder / '下一首.wav'; synthesize_audio(other)
        with patch.object(self.studio, '_save_dirty_recovery', side_effect=OSError('无法写入恢复副本')):
            self.studio.chooseAudio(str(other)); wait_idle(self.studio)
        self.assertEqual(self.studio._snapshot(), before)
        self.assertEqual(self.studio._undo, undo)
        self.assertEqual(self.studio.state['projectPath'], str(original))
        self.assertIn('恢复副本', self.studio.state['error'])

    def test_reselecting_the_same_song_keeps_lyrics_and_project(self):
        original = self.folder / '第一首.bfstudio.json'
        self.studio.saveProject(str(original))
        self.studio.updateSegment(0, '保留这句歌词')
        before = self.studio._snapshot()
        self.studio.chooseAudio(str(self.audio)); wait_idle(self.studio)
        self.assertEqual(self.studio._snapshot(), before)
        self.assertEqual(self.studio.state['projectPath'], str(original))

    def test_failed_engine_preserves_local_failure_details(self):
        self.studio._start_job('测试失败诊断')
        self.studio._launch(sys.executable, ['-c', 'import sys; sys.stderr.write("engine failed"); sys.exit(3)'],
                            lambda output: self.fail('Failed engine was accepted'))
        wait_idle(self.studio)
        diagnostic = (self.studio._job_dir / 'engine-failure.log').read_text()
        self.assertIn('exitCode=3', diagnostic)
        self.assertIn('engine failed', diagnostic)
        self.assertIn('未完成', self.studio.state['error'])

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS Qt file metadata')
    def test_export_repairs_hidden_platform_plugin_in_the_render_process(self):
        from PySide6 import __file__ as pyside_file
        platforms = Path(pyside_file).parent / 'Qt/plugins/platforms'
        if not platforms.resolve().is_relative_to(ROOT / '.venv'):
            self.skipTest('Only modify this workspace Qt runtime')
        plugin = platforms / 'libqoffscreen.dylib'
        flags = {path: path.stat().st_flags for path in (platforms, plugin)}
        before = hashlib.sha256(plugin.read_bytes()).hexdigest()
        try:
            for path, value in flags.items():
                os.chflags(path, value | stat.UF_HIDDEN)
            output = self.folder / '隐藏插件修复验收.mp4'
            self.studio.exportVideoTo(str(output)); wait_idle(self.studio)
            self.assertTrue(output.is_file(), self.studio.state['status'])
            self.assertEqual(self.studio.state['savedVideoPath'], str(output))
            self.assertEqual(hashlib.sha256(plugin.read_bytes()).hexdigest(), before)
        finally:
            for path, value in flags.items():
                os.chflags(path, value)

    def test_saving_recovery_asks_for_a_real_project_and_keeps_backup(self):
        recovery = Path(self.studio._save_dirty_recovery())
        self.studio.openProject(str(recovery))
        before = recovery.read_bytes()
        project = self.folder / '恢复后的工程.bfstudio.json'
        with patch('app.studio.QFileDialog.getSaveFileName', return_value=(str(project), '')) as dialog:
            self.studio.saveProject()
            dialog.assert_called_once()
        self.assertEqual(recovery.read_bytes(), before)
        self.assertTrue(project.is_file())
        self.assertNotIn(str(recovery), self.studio._recent_projects.paths)

    def test_export_to_renders_verifies_and_saves_in_one_action(self):
        output = self.folder / '带声音的成片'
        self.studio.exportVideoTo(str(output)); wait_idle(self.studio)
        saved = output.with_suffix('.mp4')
        self.assertEqual(self.studio.state['savedVideoPath'], str(saved), self.studio.state['status'])
        self.assertGreater(saved.stat().st_size, 1000)
        self.assertEqual(saved.read_bytes(), Path(self.studio.state['resultPath']).read_bytes())
        before = saved.read_bytes()
        with patch.object(self.studio, '_render') as render:
            self.studio.exportVideoTo(str(saved))
            render.assert_not_called()
        self.assertEqual(saved.read_bytes(), before)

        saved_url = self.studio.state['savedVideoUrl']
        self.assertTrue(saved_url)
        self.assertTrue(self.studio.state['savedVideoCurrent'])
        self.studio.setSetting('fontSize', 56)
        self.assertFalse(self.studio.state['resultUrl'])
        self.assertEqual(self.studio.state['savedVideoUrl'], saved_url)
        self.assertFalse(self.studio.state['savedVideoCurrent'])
        self.assertEqual(saved.read_bytes(), before)
        other = self.folder / '下一首.wav'; synthesize_audio(other)
        self.studio.chooseAudio(str(other)); wait_idle(self.studio)
        self.assertFalse(self.studio.state['savedVideoUrl'])
        self.assertFalse(self.studio.state['savedVideoPath'])

    def test_export_cancel_or_bad_destination_never_starts_work(self):
        with patch('app.studio.QFileDialog.getSaveFileName', return_value=('', '')):
            with patch.object(self.studio, '_render') as render:
                self.studio.exportVideoTo()
                render.assert_not_called()
        with patch.object(self.studio, '_render') as render:
            self.studio.exportVideoTo(str(self.folder / 'missing' / 'movie.mp4'))
            render.assert_not_called()
        self.assertFalse(self.studio.state['busy'])
        self.assertFalse(self.studio.state['savedVideoPath'])

    def test_recent_projects_are_bounded_and_damaged_history_is_optional(self):
        history = RecentProjects(self.folder)
        for index in range(8):
            history.remember(self.folder / f'{index}.bfstudio.json')
        self.assertEqual(len(RecentProjects(self.folder).rows()), 6)
        history.remember(self.folder / '5.bfstudio.json')
        self.assertEqual(history.paths[0], str(self.folder / '5.bfstudio.json'))
        history.path.write_text('not json')
        self.assertFalse(RecentProjects(self.folder).paths)
