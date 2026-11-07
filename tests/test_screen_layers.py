import copy
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage
from app.controller import Controller, initial_state, project_state
from app.screen_layers import (clean_layer_text, default_layers, import_logo, load_layers,
                               logo_url, save_layers, validate_layers)


class ScreenLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.controller = SimpleNamespace(runtime=self.runtime, _state=initial_state(), tick=lambda: None,
                                          notice=lambda text: None, _message_timer=None,
                                          stateChanged=SimpleNamespace(emit=lambda: None))
        for method in ('_save_screen_layers', 'setScreenText', 'setScreenLayer', 'chooseScreenLogo', 'removeScreenLogo',
                       'prepareScreenLogo', 'applyScreenContent', 'hideScreenExtras', 'clearGuestMessage', '_withdraw_screen_content', 'hideScreenContent', 'setGuestMessage', 'take_wish', 'approveGuestWish'):
            setattr(self.controller, method, MethodType(getattr(Controller, method), self.controller))

    def tearDown(self):
        self.temp.cleanup()

    def logo(self):
        path = self.runtime / '透明 Logo.png'
        image = QImage(120, 60, QImage.Format.Format_ARGB32)
        image.fill(QColor('transparent'))
        image.setPixelColor(60, 30, QColor('#FFA000'))
        self.assertTrue(image.save(str(path)))
        return path

    def test_empty_defaults_and_corrupt_file_are_hidden(self):
        self.assertEqual(load_layers(self.runtime), default_layers())
        for content in ('broken', '{"schemaVersion": 99}', '{"schemaVersion": 1, "layers": []}'):
            (self.runtime / 'screen-layers.json').write_text(content)
            self.assertEqual(load_layers(self.runtime), default_layers())

    def test_plain_text_cleaning_and_limit(self):
        self.assertEqual(clean_layer_text('  <DJ>\r\n欢迎\t你\x00  '), '<DJ>\n欢迎 你')
        self.assertEqual(len(clean_layer_text('好' * 300)), 300)
        with self.assertRaises(ValueError):
            clean_layer_text('好' * 301)
        self.assertFalse(self.controller.setScreenText(' \n '))
        self.assertFalse(self.controller._state['screenLayers']['text']['visible'])

    def test_invalid_configuration_is_rejected(self):
        for layer, key, value in (('logo', 'image', '../bad.png'), ('text', 'opacity', float('nan')),
                                  ('logo', 'size', True), ('text', 'fontSize', 121),
                                  ('text', 'color', 'red'), ('logo', 'position', 'anywhere'), ('logo', 'style', 'record'),
                                  ('logo', 'visible', True), ('text', 'visible', True)):
            candidate = default_layers()
            candidate[layer][key] = value
            with self.subTest(layer=layer, key=key, value=value), self.assertRaises(ValueError):
                validate_layers(candidate)

    def test_existing_settings_without_style_keep_original_appearance(self):
        old = default_layers()
        for row in old.values():
            del row['style']
        self.assertEqual(validate_layers(old), default_layers())
        old['text']['style'] = 'champagne'
        old['logo']['style'] = 'foil'
        save_layers(self.runtime, old)
        self.assertEqual(load_layers(self.runtime), old)

    def test_logo_file_url_prepares_a_draft_without_publishing(self):
        logo = self.logo()
        draft = self.controller.prepareScreenLogo(logo.as_uri())
        self.assertEqual(draft['fileName'], logo.name)
        self.assertTrue(draft['url'].startswith('file:///'))
        self.assertEqual(self.controller._state['screenLayers'], default_layers())

    def test_import_owns_alpha_copy_and_survives_original_removal(self):
        source = self.logo()
        name, label = import_logo(self.runtime, source)
        self.assertEqual(label, source.name)
        candidate = default_layers()
        candidate['logo'].update(image=name, fileName=label, visible=True)
        save_layers(self.runtime, candidate)
        source.unlink()
        owned = QImage(str(self.runtime / 'screen-assets' / name))
        self.assertEqual(owned.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(owned.pixelColor(60, 30).name(), '#ffa000')
        self.assertTrue(logo_url(self.runtime, load_layers(self.runtime)).startswith('file:///'))

    def test_bad_image_and_failed_import_preserve_previous_logo(self):
        self.assertTrue(self.controller.chooseScreenLogo(str(self.logo())))
        old = copy.deepcopy(self.controller._state['screenLayers'])
        invalid = self.runtime / 'bad.png'
        invalid.write_bytes(b'not an image')
        self.assertFalse(self.controller.chooseScreenLogo(str(invalid)))
        self.assertEqual(self.controller._state['screenLayers'], old)

    def test_settings_restore_independent_visibility_and_all_controls(self):
        h = self.controller
        self.assertTrue(h.setScreenText('DJ PRO\n欢迎来到现场'))
        self.assertTrue(h.chooseScreenLogo(str(self.logo())))
        for layer, key, value in (('text', 'position', 'bottom-center'), ('text', 'opacity', .7),
                                  ('text', 'fontSize', 72), ('text', 'color', '#ffcc00'),
                                  ('logo', 'size', 30), ('logo', 'position', 'top-right')):
            self.assertTrue(h.setScreenLayer(layer, key, value))
        self.assertTrue(h.setScreenLayer('text', 'visible', False))
        restored = load_layers(self.runtime)
        self.assertEqual(restored, h._state['screenLayers'])
        self.assertFalse(restored['text']['visible'])
        self.assertTrue(restored['logo']['visible'])
        self.assertEqual(restored['text']['color'], '#FFCC00')
        self.assertTrue(h.removeScreenLogo())
        self.assertFalse(h._state['screenLayers']['logo']['visible'])
        self.assertEqual(h._state['logoImageUrl'], '')
        self.assertTrue((self.runtime / '透明 Logo.png').exists())

    def test_failed_atomic_save_preserves_live_and_persisted_state(self):
        h = self.controller
        self.assertTrue(h.setScreenText('旧文字'))
        old = copy.deepcopy(h._state['screenLayers'])
        with patch('app.lyric_cache.os.replace', side_effect=OSError('disk full')):
            self.assertFalse(h.setScreenText('新文字'))
        self.assertEqual(h._state['screenLayers'], old)
        self.assertEqual(load_layers(self.runtime), old)
        self.assertIn('disk full', h._state['screenLayerNotice'])
        self.assertTrue(h.setScreenText('重试成功'))
        self.assertEqual(h._state['screenLayerNotice'], '')

    def test_missing_logo_does_not_block_text_or_hiding(self):
        h = self.controller
        self.assertTrue(h.chooseScreenLogo(str(self.logo())))
        (self.runtime / 'screen-assets' / h._state['screenLayers']['logo']['image']).unlink()
        self.assertTrue(h.setScreenText('仍可上屏'))
        self.assertTrue(h.setScreenLayer('logo', 'visible', False))
        self.assertFalse(h.setScreenLayer('logo', 'visible', True))

    def test_asset_symlinks_are_not_loaded(self):
        source = self.logo()
        name, _ = import_logo(self.runtime, source)
        candidate = default_layers()
        candidate['logo']['image'] = name
        path = self.runtime / 'screen-assets' / name
        path.unlink()
        path.symlink_to(source)
        self.assertEqual(logo_url(self.runtime, candidate), '')

    def test_import_preview_does_not_publish_or_save_configuration(self):
        h = self.controller
        before = copy.deepcopy(h._state['screenLayers'])
        asset = h.prepareScreenLogo(str(self.logo()))
        self.assertTrue(asset['url'].startswith('file:///'))
        self.assertEqual(h._state['screenLayers'], before)
        self.assertEqual(load_layers(self.runtime), default_layers())
        row = dict(before['logo'], image=asset['image'], fileName=asset['fileName'])
        self.assertTrue(h.applyScreenContent('logo', row, 0))
        self.assertTrue(h._state['screenLayers']['logo']['visible'])
        self.assertEqual(load_layers(self.runtime)['logo'], h._state['screenLayers']['logo'])

    def test_timed_text_expires_in_renderer_and_is_hidden_in_saved_profile(self):
        h = self.controller
        row = dict(default_layers()['text'], text='限时喊话')
        now = 1_000_000_000
        with patch('app.controller.monotonic_ns', return_value=now):
            self.assertTrue(h.applyScreenContent('text', row, 15))
            self.assertTrue(h.chooseScreenLogo(str(self.logo())))
        self.assertTrue(h._state['screenLayers']['text']['visible'])
        self.assertFalse(load_layers(self.runtime)['text']['visible'])
        self.assertEqual(load_layers(self.runtime)['text']['text'], '限时喊话')
        before = project_state(h._state, {}, {}, None, now + 14_500_000_000)
        expired = project_state(h._state, {}, {}, None, now + 15_000_000_000)
        self.assertEqual(before['screenTextRemaining'], 1)
        self.assertTrue(before['screenLayers']['text']['visible'])
        self.assertFalse(expired['screenLayers']['text']['visible'])
        self.assertTrue(expired['screenLayers']['logo']['visible'])

    def test_fixed_text_and_saved_logo_restore_but_temporary_text_does_not(self):
        h = self.controller
        row = dict(default_layers()['text'], text='固定欢迎词')
        self.assertTrue(h.applyScreenContent('text', row, 0))
        self.assertTrue(load_layers(self.runtime)['text']['visible'])
        self.assertEqual(h._state['screenTextValidUntilNs'], 0)
        row['text'] = '生日祝福'
        self.assertTrue(h.applyScreenContent('text', row, 15))
        self.assertFalse(load_layers(self.runtime)['text']['visible'])

    def test_failed_publish_preserves_old_output_guest_and_deadline(self):
        h = self.controller
        self.assertTrue(h.setScreenText('原内容'))
        h._state['guestMessage'] = {'visible': True, 'text': '原祝福'}
        before = copy.deepcopy(h._state)
        with patch('app.controller.save_layers', side_effect=OSError('disk full')):
            self.assertFalse(h.applyScreenContent('text', dict(default_layers()['text'], text='新内容'), 15))
        for key in ('screenLayers', 'guestMessage', 'screenTextValidUntilNs'):
            self.assertEqual(h._state[key], before[key])
        self.assertTrue(h.applyScreenContent('text', dict(default_layers()['text'], text='更新成功'), 15))
        self.assertFalse(h._state['guestMessage']['visible'])

    def test_hide_all_preserves_assets_pending_wishes_and_lyric_source(self):
        h = self.controller
        self.assertTrue(h.setScreenText('原文字'))
        self.assertTrue(h.chooseScreenLogo(str(self.logo())))
        h._state.update(guestMessage={'visible': True, 'text': '祝福'}, guestInbox=[{'id': 'pending'}],
                        guestQr={'running': True, 'onScreen': True}, selectedSource='B')
        self.assertTrue(h.hideScreenExtras())
        self.assertFalse(h._state['screenLayers']['text']['visible'])
        self.assertFalse(h._state['screenLayers']['logo']['visible'])
        self.assertFalse(h._state['guestMessage']['visible'])
        self.assertFalse(h._state['guestQr']['onScreen'])
        self.assertTrue(h._state['guestQr']['running'])
        self.assertTrue(h._state['screenLayers']['logo']['image'])
        self.assertEqual(h._state['guestInbox'], [{'id': 'pending'}])
        self.assertEqual(h._state['selectedSource'], 'B')

    def test_withdraw_still_takes_effect_if_disk_is_full(self):
        h = self.controller
        self.assertTrue(h.setScreenText('请撤下'))
        self.assertTrue(h.chooseScreenLogo(str(self.logo())))
        with patch('app.controller.save_layers', side_effect=OSError('disk full')):
            self.assertTrue(h.hideScreenExtras())
        self.assertFalse(h._state['screenLayers']['text']['visible'])
        self.assertFalse(h._state['screenLayers']['logo']['visible'])
        self.assertIn('已撤下', h._state['screenLayerNotice'])

    def test_late_logo_update_does_not_revive_expired_text(self):
        h = self.controller
        with patch('app.controller.monotonic_ns', return_value=1_000_000_000):
            self.assertTrue(h.applyScreenContent('text', dict(default_layers()['text'], text='旧喊话'), 1))
        with patch('app.controller.monotonic_ns', return_value=3_000_000_000):
            self.assertTrue(h.chooseScreenLogo(str(self.logo())))
        self.assertFalse(h._state['screenLayers']['text']['visible'])
        self.assertFalse(load_layers(self.runtime)['text']['visible'])

    def test_failed_wish_approval_keeps_pending_item_and_old_shout(self):
        h = self.controller
        self.assertTrue(h.setScreenText('原喊话'))
        wish = {'id': 'pending', 'name': '小王', 'text': '干杯'}
        h._state['guestInbox'] = [wish]
        with patch('app.controller.save_layers', side_effect=OSError('disk full')):
            h.approveGuestWish('pending')
        self.assertEqual(h._state['guestInbox'], [wish])
        self.assertTrue(h._state['screenLayers']['text']['visible'])
        self.assertFalse(h._state['guestMessage']['visible'])

    def test_guest_deadline_is_enforced_from_frozen_snapshot(self):
        state = initial_state()
        state['guestMessage'] = {'visible': True, 'text': '临时祝福'}
        state['guestMessageValidUntilNs'] = 3_000_000_000
        before = project_state(state, {}, {}, None, 2_500_000_000)
        after = project_state(state, {}, {}, None, 3_000_000_000)
        self.assertTrue(before['guestMessage']['visible'])
        self.assertEqual(before['guestMessageRemaining'], 1)
        self.assertFalse(after['guestMessage']['visible'])
        self.assertEqual(after['guestMessageRemaining'], 0)


if __name__ == '__main__':
    unittest.main()
