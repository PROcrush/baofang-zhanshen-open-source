import copy
import json
import tempfile
import unittest
import zipfile
import warnings
from pathlib import Path
from types import MethodType
from unittest.mock import patch

from app.controller import Controller
from app.screen_layers import default_layers, load_layers
from app.screen_library import (default_library, load_library,
                                validate_library, export_profile, prepare_profile)
from tests import test_screen_layers as layer_harness


class ScreenLibraryTests(unittest.TestCase):
    setUp = layer_harness.ScreenLayerTests.setUp
    tearDown = layer_harness.ScreenLayerTests.tearDown
    logo = layer_harness.ScreenLayerTests.logo

    def bind(self):
        for name in ('saveScreenPhrase', 'deleteScreenPhrase', 'prepareScreenProfile', 'applyScreenProfile', 'exportScreenProfile'):
            setattr(self.controller, name, MethodType(getattr(Controller, name), self.controller))
        return self.controller

    def test_custom_phrase_crud_and_restart(self):
        h = self.bind()
        self.assertTrue(h.saveScreenPhrase('', '我的生日', '祝{姓名}生日快乐，今晚开心！'))
        row = h._state['screenContentLibrary']['phrases'][0]
        self.assertEqual(row['text'], '祝{姓名}生日快乐，今晚开心！')
        self.assertFalse(h._state['screenLayers']['text']['visible'])
        self.assertEqual(load_library(self.runtime), h._state['screenContentLibrary'])
        self.assertTrue(h.saveScreenPhrase(row['id'], '改名', '欢迎{姓名}'))
        self.assertEqual(load_library(self.runtime)['phrases'][0]['label'], '改名')
        self.assertTrue(h.deleteScreenPhrase(row['id']))
        self.assertEqual(len(load_library(self.runtime)['phrases']), 4)

    def test_phrase_limits_and_save_failure_preserve_state(self):
        h = self.bind()
        before = copy.deepcopy(h._state['screenContentLibrary'])
        for label, text in (('', '有效'), ('长'*17, '有效'), ('标题', ' '), ('标题', '字'*301)):
            self.assertFalse(h.saveScreenPhrase('', label, text))
        with patch('app.screen_library.atomic_bytes', side_effect=OSError('read only')):
            self.assertFalse(h.saveScreenPhrase('', '保存失败', '正文'))
        self.assertEqual(h._state['screenContentLibrary'], before)
        rows = [{'id': str(i), 'label': str(i), 'text': '正文'} for i in range(21)]
        with self.assertRaises(ValueError):
            validate_library({'phrases': rows, 'history': []})

    def test_only_successful_actual_sends_enter_bounded_history(self):
        h = self.bind()
        for i in range(12):
            row = dict(default_layers()['text'], text=f'发送 {i}')
            self.assertTrue(h.applyScreenContent('text', row, 15))
        history = load_library(self.runtime)['history']
        self.assertEqual(len(history), 10)
        self.assertEqual(history[0]['row']['text'], '发送 11')
        self.assertFalse(history[0]['row']['visible'])
        self.assertFalse(load_layers(self.runtime)['text']['visible'])
        before = copy.deepcopy(h._state['screenContentLibrary'])
        with patch('app.controller.save_layers', side_effect=OSError('read only')):
            self.assertFalse(h.applyScreenContent('text', dict(default_layers()['text'], text='未发布'), 15))
        self.assertEqual(h._state['screenContentLibrary'], before)
        self.assertFalse(h.applyScreenContent('text', dict(default_layers()['text'], text='祝{姓名}生日快乐'), 15))

    def test_history_save_failure_does_not_report_publish_failure(self):
        h = self.bind()
        with patch('app.controller.save_library', side_effect=OSError('read only')):
            self.assertTrue(h.applyScreenContent('text', dict(default_layers()['text'], text='已上屏'), 15))
        self.assertTrue(h._state['screenLayers']['text']['visible'])
        self.assertEqual(h._state['screenContentLibrary']['history'], [])
        self.assertIn('已上屏', h._state['screenLayerNotice'])

    def test_legacy_settings_migrate_and_layer_changes_keep_library(self):
        (self.runtime/'screen-layers.json').write_text(json.dumps({'schemaVersion': 1, 'layers': default_layers()}))
        self.assertEqual(load_library(self.runtime), default_library())
        h = self.bind()
        self.assertTrue(h.saveScreenPhrase('', '个人', '我的常用语'))
        self.assertTrue(h.setScreenText('固定文字'))
        self.assertTrue(h.chooseScreenLogo(str(self.logo())))
        self.assertEqual(load_library(self.runtime)['phrases'][0]['label'], '个人')
        self.assertEqual(load_layers(self.runtime), h._state['screenLayers'])

    def test_profile_roundtrip_contains_only_layout_phrases_and_owned_logo(self):
        h = self.bind()
        self.assertTrue(h.chooseScreenLogo(str(self.logo())))
        self.assertTrue(h.applyScreenContent('text', dict(default_layers()['text'], text='李先生私有生日记录'), 15))
        (self.runtime/'settings.json').write_text('{"apiKey":"DO_NOT_EXPORT"}')
        pack = self.runtime/'DJ.bfdj.zip'
        export_profile(self.runtime, h._state['screenLayers'], h._state['screenContentLibrary']['phrases'], pack)
        with zipfile.ZipFile(pack) as archive:
            self.assertEqual(set(archive.namelist()), {'profile.json', 'logo.png'})
            manifest = json.loads(archive.read('profile.json'))
            self.assertEqual(set(manifest), {'kind', 'schemaVersion', 'layers', 'phrases'})
            self.assertNotIn('李先生', archive.read('profile.json').decode())
            self.assertNotIn('DO_NOT_EXPORT', archive.read('profile.json').decode())
        with tempfile.TemporaryDirectory() as destination:
            result = prepare_profile(destination, pack)
            self.assertTrue(result['logoImageUrl'])
            self.assertFalse(result['layers']['text']['visible'])
            self.assertEqual(result['layers']['text']['text'], '')
            self.assertEqual(load_layers(destination), default_layers(), 'preparation changed live settings')

    def test_atomic_profile_application_and_history_preservation(self):
        h = self.bind()
        self.assertTrue(h.applyScreenContent('text', dict(default_layers()['text'], text='本机历史'), 15))
        before = copy.deepcopy(h._state)
        imported = default_layers()
        imported['text']['fontSize'] = 72
        phrases = [{'id': 'new', 'label': '新常用语', 'text': '欢迎{姓名}'}]
        with patch('app.controller.save_profile', side_effect=OSError('read only')):
            self.assertFalse(h.applyScreenProfile(imported, phrases))
        self.assertEqual(h._state['screenLayers'], before['screenLayers'])
        self.assertEqual(h._state['screenContentLibrary'], before['screenContentLibrary'])
        self.assertTrue(h.applyScreenProfile(imported, phrases))
        self.assertEqual(h._state['screenLayers']['text']['fontSize'], 72)
        self.assertFalse(h._state['screenLayers']['text']['visible'])
        self.assertEqual(load_library(self.runtime)['phrases'], phrases)
        self.assertEqual(load_library(self.runtime)['history'], before['screenContentLibrary']['history'])

    def test_rejects_paths_duplicates_bad_checksums_and_missing_logo(self):
        h = self.bind()
        self.assertTrue(h.chooseScreenLogo(str(self.logo())))
        path = self.runtime/'good.zip'
        export_profile(self.runtime, h._state['screenLayers'], default_library()['phrases'], path)
        with zipfile.ZipFile(path) as archive:
            manifest, logo = archive.read('profile.json'), archive.read('logo.png')
        for entries in ([('profile.json', manifest), ('../evil', b'x')],
                        [('profile.json', manifest)],
                        [('profile.json', manifest), ('logo.png', b'bad')],
                        [('profile.json', manifest), ('profile.json', manifest), ('logo.png', logo)]):
            invalid = self.runtime/'bad.zip'
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(invalid, 'w') as archive:
                    for name, data in entries:
                        archive.writestr(name, data)
            with self.assertRaises(ValueError):
                prepare_profile(self.runtime, invalid)
        self.assertFalse((self.runtime.parent/'evil').exists())

    def test_atomic_export_failure_preserves_existing_file(self):
        target = self.runtime/'old.bfdj.zip'
        target.write_bytes(b'previous export')
        with patch('app.lyric_cache.os.replace', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                export_profile(self.runtime, default_layers(), default_library()['phrases'], target)
        self.assertEqual(target.read_bytes(), b'previous export')

    def test_oversized_and_unknown_manifest_fields_are_rejected(self):
        path = self.runtime/'large.zip'
        with path.open('wb') as handle:
            handle.truncate(24_000_001)
        with self.assertRaises(ValueError):
            prepare_profile(self.runtime,path)
        export_profile(self.runtime, default_layers(), default_library()['phrases'], path)
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read('profile.json'))
        manifest['history'] = []
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('profile.json',json.dumps(manifest))
        with self.assertRaises(ValueError):
            prepare_profile(self.runtime,path)

    def test_fixed_send_clears_previous_countdown(self):
        h = self.bind()
        self.assertTrue(h.applyScreenContent('text', dict(default_layers()['text'], text='限时'), 15))
        self.assertEqual(h._state['screenTextRemaining'], 15)
        self.assertTrue(h.applyScreenContent('text', dict(default_layers()['text'], text='固定'), 0))
        self.assertEqual(h._state['screenTextRemaining'], 0)

    def test_phrase_save_keeps_current_hidden_state_not_stale_disk_visibility(self):
        h = self.bind()
        self.assertTrue(h.setScreenText('固定内容'))
        self.assertTrue(load_layers(self.runtime)['text']['visible'])
        h._state['screenLayers']['text']['visible'] = False
        self.assertTrue(h.saveScreenPhrase('', '新常用语', '正文'))
        self.assertFalse(load_layers(self.runtime)['text']['visible'])


if __name__ == '__main__':
    unittest.main()
