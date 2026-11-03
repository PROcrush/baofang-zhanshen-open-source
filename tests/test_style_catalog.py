"""Prepared preset palettes survive edits, persistence and output validation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from app.controller import Controller, initial_state
from app.messages import validate_program_payload
from app.style_catalog import style_catalog
from app.styles import DEFAULT_STYLE, changed_style, style_preset, validate_style


class StyleCatalogTests(unittest.TestCase):
    def test_all_reference_cards_and_palettes_are_local_complete_recipes(self):
        catalog = style_catalog()
        self.assertEqual(len(catalog), 27)
        self.assertEqual(sum(len(item['schemes']) for item in catalog), 88)
        self.assertEqual(len({item['id'] for item in catalog}), 27)
        self.assertLessEqual(len({item['category'] for item in catalog}), 6)
        self.assertEqual(catalog[0]['name'], '黑白色差')
        self.assertEqual(catalog[-1]['name'], '金色之夜')
        for item in catalog:
            with self.subTest(preset=item['id']):
                self.assertEqual(item['previewStyle'], style_preset(item['id']))
            for index, palette in enumerate(item['schemes']):
                with self.subTest(preset=item['id'], palette=index):
                    style = style_preset(item['id'], scheme_index=index)
                    self.assertEqual(validate_style(style), style)
                    self.assertEqual(style['presetId'], item['id'])
                    self.assertEqual(style['schemeIndex'], index)
                    self.assertFalse(style['presetCustomized'])
                    self.assertEqual(style['decorationMode'], item['decorationMode'])
                    self.assertEqual(style['backgroundColor'], palette['bg'])
                    self.assertEqual(style['highlightColor'], palette['accent'])
                    self.assertEqual(style['nextColor'], palette['sub'])
                    self.assertEqual(style['decorationColor'], palette['accent2'])
                    self.assertTrue(style['dynamicBackground'])
                    self.assertEqual(style['baseColor'], palette.get('grad', [palette['fg']])[0])
                    state = initial_state()
                    state['style'] = style
                    payload = {'state': state, 'raw': {}, 'timelines': {}}
                    self.assertEqual(validate_program_payload(payload)['state']['style'], style)

    def test_specific_reference_gradient_and_paper_data_are_applied(self):
        sunset = style_preset('jizura-sunset')
        self.assertTrue(sunset['gradientEnabled'])
        self.assertEqual(sunset['baseColor'], '#FFC15E')
        self.assertEqual(sunset['gradientColor'], '#FF4A7A')
        self.assertEqual(sunset['fontFamily'], 'Songti SC')
        paper = style_preset('jizura-paper')
        self.assertFalse(paper['gradientEnabled'])
        self.assertEqual(paper['outlineColor'], '#ECE9E3')
        self.assertFalse(paper['shadowEnabled'])
        self.assertEqual(style_preset('jizura-ocean')['fontFamily'], 'PingFang SC')

    def test_catalog_consumers_cannot_modify_other_previews_or_future_palettes(self):
        first = style_catalog()
        expected = copy.deepcopy(first)
        first[0]['previewStyle']['fontSize'] = 96
        first[0]['schemes'][0]['bg'] = '#FF0000'
        first[0]['schemes'][0]['colors']['bg'] = '#FF0000'
        self.assertEqual(style_catalog(), expected)

    def test_live_edits_mark_custom_and_keep_preset_available_for_color_switching(self):
        selected = style_preset('jizura-gold', scheme_index=2)
        edit = changed_style(selected, 'fontSize', 64)
        self.assertTrue(edit['presetCustomized'])
        self.assertEqual(edit['presetId'], 'jizura-gold')
        self.assertEqual(edit['schemeIndex'], 2)
        reset = style_preset(edit['presetId'], edit, 1)
        self.assertFalse(reset['presetCustomized'])
        self.assertEqual(reset['schemeIndex'], 1)
        self.assertNotEqual(reset['fontSize'], 64)
        self.assertFalse(changed_style(selected, 'brightness', .5)['presetCustomized'])
        self.assertFalse(changed_style(selected, 'countdownEnabled', False)['presetCustomized'])

    def test_old_quick_presets_clear_new_effects_and_keep_operator_preferences(self):
        current = style_preset('jizura-gold')
        current.update(fontFamily='Songti SC', brightness=.5, countdownEnabled=False)
        for name in ('classic', 'gold', 'neon', 'minimal'):
            with self.subTest(preset=name):
                selected = style_preset(name, current)
                self.assertEqual(selected['presetId'], name)
                self.assertEqual(selected['schemeIndex'], 0)
                self.assertEqual(selected['decorationMode'], 'none')
                self.assertFalse(selected['gradientEnabled'])
                self.assertEqual(selected['backgroundColor'], '#000000')
                self.assertEqual(selected['fontFamily'], 'Songti SC')
                self.assertEqual(selected['brightness'], .5)
                self.assertFalse(selected['countdownEnabled'])
                self.assertTrue(selected['dynamicBackground'])

    def test_invalid_bookkeeping_and_effects_are_rejected(self):
        for preset_id, index in (('unknown', 0), ('jizura-noir', 2),
                                 ('jizura-gold', -1), ('jizura-gold', True),
                                 ('jizura-gold', 1.0), ('classic', 1), ([], 0)):
            with self.subTest(preset=preset_id, index=index), self.assertRaises(ValueError):
                style_preset(preset_id, scheme_index=index)
        cases = [('presetId', 'not-a-preset'), ('presetId', []), ('schemeIndex', True),
                 ('schemeIndex', 1), ('schemeIndex', 1.0), ('presetCustomized', 1),
                 ('decorationMode', 'fake'), ('decorationMode', []), ('decorationEnabled', 1),
                 ('gradientEnabled', 'true'), ('gradientColor', '#abc'),
                 ('backgroundColor', '#GGGGGG'), ('decorationColor', 'red')]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_style(dict(DEFAULT_STYLE, **{key: value}))
        for key in ('presetId', 'schemeIndex', 'presetCustomized'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                changed_style(DEFAULT_STYLE, key, DEFAULT_STYLE[key])


class StylePresetPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller = SimpleNamespace(_state=initial_state(), runtime=Path(self.temp.name),
            settings_path=Path(self.temp.name) / 'settings.json')
        self.controller.tick = lambda: None
        self.controller.notice = lambda message: self.controller._state.update(notice=message)
        for name in ('save_settings', 'setStylePreset', 'setStylePresetScheme', 'setStyle'):
            setattr(self.controller, name, MethodType(getattr(Controller, name), self.controller))

    def tearDown(self):
        self.temp.cleanup()

    def test_saved_palette_reloads_as_same_complete_output_style(self):
        self.controller._state['style'].update(brightness=.55, countdownEnabled=False)
        self.controller.setStylePresetScheme('jizura-sunset', 3)
        self.controller.setStyle('decorationEnabled', False)
        saved = json.loads(self.controller.settings_path.read_text())
        loaded = validate_style(saved['style'])
        self.assertEqual(loaded, self.controller._state['style'])
        self.assertEqual(loaded['schemeIndex'], 3)
        self.assertEqual(loaded['brightness'], .55)
        self.assertFalse(loaded['countdownEnabled'])
        self.assertFalse(loaded['decorationEnabled'])
        self.assertTrue(loaded['presetCustomized'])
        self.assertFalse((self.controller.runtime / 'settings.tmp').exists())

    def test_preset_write_failure_and_invalid_index_preserve_previous_saved_style(self):
        self.controller.setStylePreset('classic')
        before = self.controller.settings_path.read_bytes()
        previous = copy.deepcopy(self.controller._state['style'])
        self.controller.setStylePresetScheme('jizura-noir', 7)
        self.assertEqual(self.controller._state['style'], previous)
        self.assertEqual(self.controller.settings_path.read_bytes(), before)
        with patch('app.controller.os.replace', side_effect=OSError('disk unavailable')):
            self.controller.setStylePresetScheme('jizura-gold', 3)
        self.assertEqual(self.controller._state['style'], previous)
        self.assertEqual(self.controller.settings_path.read_bytes(), before)
        self.assertIn('disk unavailable', self.controller._state['notice'])


if __name__ == '__main__':
    unittest.main()
