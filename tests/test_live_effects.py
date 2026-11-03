"""Source-time transitions preserve fixed slots, sweep and transparent output."""
import copy
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from app.live_effects import LIVE_EFFECTS, transition_at
from app.live_karaoke import LiveKaraokeItem
from app.lyric_paint import GradientLyricItem
from app.lyrics import project_lyrics
from app.styles import DEFAULT_STYLE, changed_style, style_preset, traditional_style, validate_style
from test_live_karaoke import APPLICATION, rgba


SOURCE = {'quality': 'sentence', 'segments': [
    {'text': '灯光照亮每一次相聚', 'startMs': 1000, 'endMs': 4000},
    {'text': '让熟悉的旋律陪伴此刻', 'startMs': 4000, 'endMs': 7000},
    {'text': '跟着节奏一起唱', 'startMs': 7500, 'endMs': 10000}]}


def rendered(item):
    image = QImage(round(item.width()), round(item.height()), QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        item.paint(painter)
    finally:
        painter.end()
    return image


class LiveEffectTests(unittest.TestCase):
    def item(self):
        item = LiveKaraokeItem()
        item.setWidth(854); item.setHeight(480)
        item.settings = traditional_style(DEFAULT_STYLE)
        return item

    def test_independent_validated_preferences_upgrade_and_survive_all_presets(self):
        legacy = {key:value for key,value in DEFAULT_STYLE.items() if key not in ('liveEntrance','liveExit')}
        self.assertEqual(validate_style(legacy)['liveEntrance'], 'none')
        self.assertEqual(validate_style(legacy)['liveExit'], 'none')
        selected = changed_style(changed_style(legacy, 'liveEntrance', 'ripple'), 'liveExit', 'glitch')
        from app.style_catalog import style_catalog
        for recipe in style_catalog():
            style = style_preset(recipe['id'], selected)
            self.assertEqual((style['liveEntrance'],style['liveExit']), ('ripple','glitch'))
        self.assertEqual(traditional_style(selected)['liveEntrance'], 'ripple')
        for value in ('unknown', 1, None, True):
            with self.assertRaises(ValueError):
                changed_style(selected, 'liveExit', value)

    def test_automatic_windows_do_not_overlap_and_invalid_clocks_do_not_animate(self):
        style = dict(DEFAULT_STYLE, liveEntrance='ripple', liveExit='glitch')
        for position, expected in ((0,'ripple'),(20,'ripple'),(25,None),(50,None),(75,None),(80,'glitch'),(99,'glitch')):
            result = transition_at(style, dict(startMs=0,endMs=100,positionMs=position))
            self.assertEqual(result[0] if result else None, expected)
        for clock in (None, {}, dict(startMs=0,endMs=0,positionMs=0),
                      dict(startMs=0,endMs=1,positionMs=float('nan'))):
            self.assertIsNone(transition_at(style, clock))
        self.assertIsNone(transition_at(dict(style,liveEntrance='none'), dict(startMs=0,endMs=100,positionMs=10)))
        self.assertEqual(transition_at(style,dict(startMs=0,endMs=100,positionMs=-100))[1],0)

    def test_four_distinct_effects_apply_to_both_edges_and_clear_between_them(self):
        item = self.item()
        for field,position in (('liveEntrance',150),('liveExit',3850)):
            images=[]
            for effect in LIVE_EFFECTS:
                item.settings = dict(traditional_style(DEFAULT_STYLE), **{field:effect})
                item.lyric = project_lyrics(SOURCE, position, traditional=True)
                image = rendered(item)
                images.append(rgba(image))
                self.assertEqual(image.pixelColor(0,0).alpha(), 0)
                self.assertEqual(image.pixelColor(853,479).alpha(), 0)
                item.lyric = project_lyrics(SOURCE, 1800, traditional=True)
                stable=rgba(rendered(item))
                item.settings = traditional_style(DEFAULT_STYLE)
                self.assertEqual(stable, rgba(rendered(item)))
            self.assertEqual(len(set(images)), 5, field)

    def test_exit_leaves_next_slot_identical_and_promotion_does_not_replay_entrance(self):
        item = self.item()
        item.lyric = project_lyrics(SOURCE,3850,traditional=True)
        ordinary = rendered(item)
        item.settings = dict(item.settings,liveExit='glitch',liveEntrance='ripple')
        altered = rendered(item)
        self.assertEqual(rgba(ordinary.copy(0,395,854,85)), rgba(altered.copy(0,395,854,85)))
        item.lyric = project_lyrics(SOURCE,4100,traditional=True)
        clock=item.lyric['karaoke']
        self.assertEqual(clock['appearanceMs'],0)
        self.assertEqual(clock['followingAppearanceMs'],4000)
        self.assertIsNone(transition_at(item.settings,dict(startMs=clock['appearanceMs'],endMs=7000,positionMs=4100)))
        # The newly previewed third line enters, the already shown second stays.
        self.assertEqual(transition_at(item.settings,dict(startMs=4000,endMs=10000,positionMs=4100))[0],'ripple')

    def test_same_source_position_is_identical_after_pause_speed_change_and_seek(self):
        source = copy.deepcopy(SOURCE)
        item = self.item()
        for effect in LIVE_EFFECTS[1:]:
            item.settings = dict(item.settings,liveEntrance=effect,liveExit=effect)
            expected=[]
            for position in (150,3850):
                item.lyric=project_lyrics(source,position,traditional=True)
                expected.append(rgba(rendered(item)))
            for position in (8000,0,3990,150,3850,150,3850):
                item.lyric=project_lyrics(source,position,traditional=True)
                if position in (150,3850):
                    self.assertEqual(rgba(rendered(item)),expected[(150,3850).index(position)])
            item.lyric={'text':'','visible':False}
            self.assertEqual(max(rgba(rendered(item))[3::4]),0)
        self.assertEqual(source,SOURCE)

    def test_generic_styles_use_same_clock_and_compositor_with_sweep_off_or_on(self):
        item = GradientLyricItem()
        item.setWidth(700); item.setHeight(100)
        item.text=SOURCE['segments'][0]['text'];item._rebuild()
        for sweep in (False, True):
            projected=project_lyrics(SOURCE,1150,sweep=sweep)
            self.assertEqual(projected['effectClock'],dict(startMs=1000,endMs=4000,positionMs=1150))
            item.sweepEnabled=sweep;item.sweepProgress=.3
            images=[]
            for effect in LIVE_EFFECTS:
                item.effectSettings=dict(liveEntrance=effect)
                item.effectClock=projected['effectClock']
                images.append(rgba(rendered(item)))
            self.assertEqual(len(set(images)),5)


if __name__ == '__main__':
    unittest.main()
