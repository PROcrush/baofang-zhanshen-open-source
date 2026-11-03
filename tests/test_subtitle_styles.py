"""Local live subtitle settings cross persistence and output without hidden defaults."""
import unittest

from app.styles import DEFAULT_STYLE, changed_style, style_preset, validate_style

LEGACY_STYLE = {'fontFamily': 'PingFang SC', 'fontSize': 48, 'letterSpacing': 0,
                'brightness': 1, 'sweepMode': 'word', 'outputMode': 'standard',
                'dynamicBackground': False, 'countdownEnabled': True}


class SubtitleStyleTests(unittest.TestCase):
    def test_live_text_color_can_be_changed_and_validated_for_output(self):
        style = changed_style(dict(DEFAULT_STYLE), 'baseColor', '#11AABB')
        self.assertEqual(validate_style(style)['baseColor'], '#11AABB')
        self.assertNotEqual(DEFAULT_STYLE.get('baseColor'), '#11AABB')

    def test_existing_saved_preferences_upgrade_without_losing_user_values(self):
        old = dict(LEGACY_STYLE, fontSize=72, brightness=.6)
        upgraded = validate_style(old)
        self.assertEqual(upgraded['fontSize'], 72)
        self.assertEqual(upgraded['brightness'], .6)
        self.assertIn('verticalPosition', upgraded)
        self.assertEqual(old, dict(LEGACY_STYLE, fontSize=72, brightness=.6))

    def test_live_geometry_and_text_effect_fields_reach_output_contract(self):
        style = dict(DEFAULT_STYLE)
        edits = {'highlightColor': '#9AE0FF', 'nextColor': '#ABCDEF', 'outlineColor': '#000000',
                 'outlineWidth': 3, 'shadowEnabled': True, 'shadowColor': '#111111',
                 'shadowBlur': 8, 'shadowOffsetX': -2, 'shadowOffsetY': 4,
                 'verticalPosition': 75, 'horizontalMargin': 12, 'lineMode': 'karaoke'}
        for key, value in edits.items():
            style = changed_style(style, key, value)
        for key, value in edits.items():
            self.assertEqual(validate_style(style)[key], value)

    def test_invalid_colors_geometry_and_unknown_fields_fail_before_persistence(self):
        cases = [('baseColor', 'not-a-color'), ('highlightColor', '#GGGGGG'),
                 ('outlineWidth', 9), ('shadowBlur', float('nan')), ('shadowEnabled', 1),
                 ('verticalPosition', 99), ('horizontalMargin', 40), ('lineMode', 'fake-word'),
                 ('lineSpacing', 100), ('fontBold', 1), ('textAlignment', 'diagonal'),
                 ('unknownOption', True)]
        for key, value in cases:
            with self.subTest(key=key), self.assertRaises(ValueError):
                changed_style(DEFAULT_STYLE, key, value)

    def test_unknown_style_fields_are_not_silently_forwarded(self):
        with self.assertRaises(ValueError):
            validate_style(dict(LEGACY_STYLE, untrustedStyle='bad'))

    def test_presets_replace_appearance_and_keep_venue_background_and_font(self):
        custom = dict(DEFAULT_STYLE, fontFamily='Songti SC', brightness=.5,
                      dynamicBackground=True, textAlignment='right', fontBold=False)
        for name, mode in (('classic', 'karaoke'), ('gold', 'double'),
                           ('neon', 'karaoke'), ('minimal', 'single')):
            with self.subTest(name=name):
                selected = style_preset(name, custom)
                self.assertEqual(selected['lineMode'], mode)
                self.assertEqual(selected['fontFamily'], 'Songti SC')
                self.assertEqual(selected['brightness'], .5)
                self.assertTrue(selected['dynamicBackground'])
                self.assertTrue(selected['fontBold'])
                self.assertEqual(selected['textAlignment'], 'center')
                self.assertEqual(validate_style(selected), selected)
        self.assertEqual(custom['textAlignment'], 'right')
        with self.assertRaises(ValueError):
            style_preset('unknown', custom)


if __name__ == '__main__':
    unittest.main()
