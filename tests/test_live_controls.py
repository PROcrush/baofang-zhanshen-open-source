"""Live controls preserve media versions, readonly clocks and hidden-source state."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from app.controller import Controller, initial_state, project_state
from app.live_settings import validate_offsets, validate_output_target
from app.messages import validate_program_payload
from app.native import NativeClock
from app.styles import style_preset, traditional_style
from test_controller_modes import NOW, replay, timeline


class LiveControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.h = SimpleNamespace(_state=initial_state(), runtime=Path(self.temp.name),
                                 settings_path=Path(self.temp.name) / 'settings.json')
        self.h._state.update(stale=False, activeDeck='A', lyricAdjustmentDeck='B')
        for key in ('A', 'B'):
            self.h._state['deck'+key].update(valid=True, cacheState='ready', versionId='demo:'+key)
        self.h.tick = lambda: None
        self.h.notice = lambda message: self.h._state.update(notice=message)
        for name in ('save_settings', 'selectSource', 'clearOutput', 'restoreOutput', 'setLyricOffset', 'setLiveLayout'):
            setattr(self.h, name, MethodType(getattr(Controller, name), self.h))

    def tearDown(self):
        self.temp.cleanup()

    def test_live_effect_preferences_persist_atomically_and_retry_after_failure(self):
        self.h.setStyle=MethodType(Controller.setStyle,self.h)
        self.h.setStyle('liveEntrance','ripple')
        saved=self.h.settings_path.read_bytes()
        self.assertEqual(json.loads(saved)['style']['liveEntrance'],'ripple')
        with patch('app.controller.os.replace',side_effect=OSError('disk unavailable')):
            self.h.setStyle('liveExit','glitch')
        self.assertEqual(self.h._state['style']['liveExit'],'none')
        self.assertEqual(self.h.settings_path.read_bytes(),saved)
        self.assertIn('字幕特效未保存',self.h._state['notice'])
        self.h.setStyle('liveExit','glitch')
        self.assertEqual(self.h._state['notice'],'')
        self.assertEqual(json.loads(self.h.settings_path.read_text())['style']['liveExit'],'glitch')

    def test_hide_twice_then_restore_keeps_manual_b_source(self):
        self.h.selectSource('B')
        self.h.clearOutput()
        self.h.clearOutput()
        self.assertEqual(self.h._state['selectedSource'], 'none')
        self.h.restoreOutput()
        self.assertEqual(self.h._state['selectedSource'], 'B')
        self.h.selectSource('auto')
        self.h.clearOutput()
        self.h.restoreOutput()
        self.assertEqual(self.h._state['selectedSource'], 'auto')

    def test_review_warning_survives_projection_without_interrupting_valid_lyrics(self):
        state = initial_state()
        state.update(mode='serato', selectedSource='A')
        raw = replay()['decks']
        raw['A'].update(identityReady=True, identityProof={})
        clock = NativeClock(Path(__file__).resolve().parents[1])
        for correction, needs_review in (({'status':'needs_review'}, True),
                                          ({'status':'ready', 'review':['check text']}, True),
                                          ({'status':'ready', 'review':[]}, False), (None, False)):
            with self.subTest(correction=correction), \
                    patch('app.controller.proof_is_current', return_value=True):
                lyrics = dict(timeline(), correction=correction)
                result = project_state(state, raw, {'demo:A':lyrics}, clock, NOW)
                self.assertEqual(result['deckA']['needsReview'], needs_review)
                self.assertEqual('待复核' in result['deckA']['cacheLabel'], needs_review)
                self.assertEqual(result['deckA']['cacheState'], 'ready')
                self.assertTrue(result['lyric']['visible'])
                self.assertNotIn('needsReview', raw['A'])
                suspended = project_state(result, {}, {'demo:A':lyrics}, clock, NOW)
                self.assertFalse(suspended['deckA']['needsReview'])
                self.assertFalse(suspended['lyric']['visible'])

    def test_layout_changes_both_modes_once_and_preserves_custom_appearance(self):
        for preset in ('minimal', 'classic', 'gold', 'jizura-noir'):
            for layout in ('single', 'double'):
                with self.subTest(preset=preset, layout=layout):
                    before = style_preset(preset)
                    before.update(baseColor='#12ABCD', fontSize=70, brightness=.6)
                    self.h._state['style'] = before
                    with patch.object(self.h, 'save_settings', wraps=self.h.save_settings) as save:
                        self.h.setLiveLayout(layout)
                    self.assertEqual(save.call_count, 1)
                    selected = self.h._state['style']
                    self.assertEqual(selected['lineMode'], layout)
                    self.assertEqual(selected['outputMode'], 'dynamic' if layout == 'single' else 'standard')
                    for key in ('baseColor', 'fontSize', 'brightness', 'sweepMode'):
                        self.assertEqual(selected[key], before[key])
                    self.assertEqual(json.loads(self.h.settings_path.read_text())['style'], selected)

    def test_failed_layout_save_keeps_both_previous_modes_and_can_retry(self):
        self.h._state['style'] = style_preset('minimal')
        self.h.save_settings()
        original = self.h._state['style']
        saved = self.h.settings_path.read_bytes()
        with patch('app.controller.os.replace', side_effect=OSError('disk unavailable')):
            self.h.setLiveLayout('double')
        self.assertEqual(self.h._state['style'], original)
        self.assertEqual(self.h.settings_path.read_bytes(), saved)
        self.assertIn('字幕布局未保存', self.h._state['notice'])
        self.h.setLiveLayout('double')
        self.assertEqual(self.h._state['style']['lineMode'], 'double')
        self.assertEqual(self.h._state['notice'], '')
        saved = self.h.settings_path.read_bytes()
        self.h.setLiveLayout('unknown')
        self.assertEqual(self.h.settings_path.read_bytes(), saved)

    def test_traditional_layout_uses_studio_appearance_and_keeps_venue_preferences(self):
        from worker.style_catalog import TRADITIONAL_KARAOKE_STYLE
        self.h._state['style'].update(brightness=.6, dynamicBackground=True, countdownEnabled=True)
        with patch.object(self.h, 'save_settings', wraps=self.h.save_settings) as save:
            self.h.setLiveLayout('karaoke')
        self.assertEqual(save.call_count, 1)
        style = self.h._state['style']
        for live, studio in (('baseColor','textColor'), ('highlightColor','highlightColor'),
                             ('outlineColor','strokeColor'), ('outlineWidth','strokeWidth'),
                             ('fontFamily','fontFamily'), ('fontSize','fontSize')):
            self.assertEqual(style[live], TRADITIONAL_KARAOKE_STYLE[studio])
        self.assertEqual(style['lineMode'], 'karaoke')
        self.assertEqual(style['outputMode'], 'standard')
        self.assertEqual(style['sweepMode'], 'word')
        self.assertFalse(style['countdownEnabled'])
        self.assertEqual(style['brightness'], .6)
        self.assertTrue(style['dynamicBackground'])

    def test_traditional_predisplay_cannot_bypass_hidden_stale_version_or_source_gates(self):
        state = initial_state()
        state.update(mode='serato', selectedSource='A', style=traditional_style(state['style']))
        raw = replay()['decks']
        for row in raw.values():
            row.update(identityReady=True, identityProof={})
        clock = NativeClock(Path(__file__).resolve().parents[1])
        with patch('app.controller.proof_is_current', return_value=True):
            ready = project_state(state, raw, {'demo:A':timeline()}, clock, NOW)
            self.assertTrue(ready['lyric']['visible'])
            self.assertIn('karaoke', ready['lyric'])
            self.assertEqual(ready['lyric']['quality'], 'sentence')
            for selected, now, timelines in (('none', NOW, {'demo:A':timeline()}),
                                              ('A', NOW+350_000_001, {'demo:A':timeline()}),
                                              ('B', NOW, {'demo:A':timeline()}), ('A', NOW, {}),
                                              ('A', NOW, {'demo:A':dict(timeline(), versionId='other:version')})):
                state['selectedSource'] = selected
                blocked = project_state(state, raw, timelines, clock, now)
                self.assertFalse(blocked['lyric']['visible'])
                self.assertNotIn('karaoke', blocked['lyric'])
            state.update(selectedSource='A', lyricOffsets={'demo:A':200})
            delayed = project_state(state, raw, {'demo:A':timeline()}, clock, NOW)
            self.assertEqual(delayed['lyric']['karaoke']['positionMs'], 1050)
            self.assertEqual(delayed['deckA']['positionMs'], 1250)
    def test_offset_saved_for_lyric_b_not_playback_a_and_zero_removes_it(self):
        self.h.setLyricOffset(300)
        settings = json.loads(self.h.settings_path.read_text())
        self.assertEqual(settings['lyricOffsets'], {'demo:B': 300})
        self.assertEqual(self.h._state['deckA']['positionMs'], 0)
        self.h.setLyricOffset(0)
        self.assertEqual(json.loads(self.h.settings_path.read_text())['lyricOffsets'], {})

    def test_unready_source_and_invalid_offsets_never_write_preferences(self):
        self.h._state['stale'] = True
        self.h.setLyricOffset(300)
        self.h._state['stale'] = False
        for bad in (3001, -3001, True, False, 1.5):
            self.h.setLyricOffset(bad)
            self.assertEqual(self.h._state['lyricOffsets'], {})
        self.assertFalse(self.h.settings_path.exists())

    def test_write_failure_keeps_prior_offset_and_settings(self):
        self.h.setLyricOffset(200)
        before = self.h.settings_path.read_bytes()
        with patch('app.controller.os.replace', side_effect=OSError('disk unavailable')):
            self.h.setLyricOffset(300)
        self.assertEqual(self.h._state['lyricOffsets'], {'demo:B': 200})
        self.assertEqual(self.h.settings_path.read_bytes(), before)
        self.assertIn('未保存', self.h._state['notice'])

    def test_positive_delay_changes_lyric_query_without_changing_source_clock(self):
        state = initial_state()
        state.update(activeDeck='A', lyricOffsets={'demo:A': 400})
        raw = replay()['decks']
        timelines = {'demo:A': timeline()}
        clock = NativeClock(Path(__file__).resolve().parents[1])
        delayed = project_state(state, raw, timelines, clock, NOW)
        self.assertEqual(delayed['deckA']['positionMs'], 1250)
        self.assertFalse(delayed['lyric']['visible'])
        self.assertEqual(delayed['lyric']['countdownMs'], 150)
        state['lyricOffsets']['demo:A'] = -400
        advanced = project_state(state, raw, timelines, clock, NOW)
        self.assertEqual(advanced['deckA']['positionMs'], 1250)
        self.assertTrue(advanced['lyric']['visible'])
        raw['A']['versionId'] = 'another:version'
        switched = project_state(state, raw, timelines, clock, NOW)
        self.assertEqual(switched['lyricOffsetMs'], 0)
        self.assertFalse(switched['lyric']['visible'])

    def test_offset_cannot_resurrect_stale_or_manually_hidden_lyrics_in_renderer(self):
        state = initial_state()
        state.update(activeDeck='A', lyricOffsets={'demo:A': -400})
        raw = replay()['decks']
        clock = NativeClock(Path(__file__).resolve().parents[1])
        for selected, now in (('none', NOW), ('auto', NOW+350_000_001)):
            state['selectedSource'] = selected
            projected = project_state(state, raw, {'demo:A': timeline()}, clock, now)
            self.assertFalse(projected['lyric']['visible'])

    def test_renderer_rejects_bad_live_settings_before_using_them(self):
        payload = {'state':initial_state(), 'raw':replay()['decks'], 'timelines':{'demo:A':timeline()}}
        for name, bad in (('lyricOffsets', {'demo:A':float('nan')}),
                          ('lyricOffsets', {'demo:A':True}),
                          ('lyricOffsets', {'demo:A':3001}),
                          ('outputTarget', {'screenName':'', 'fullScreen':'true'}),
                          ('resumeSource', 'none')):
            candidate = copy.deepcopy(payload)
            candidate['state'][name] = bad
            with self.subTest(name=name, bad=bad), self.assertRaises(ValueError):
                validate_program_payload(candidate)

    def test_saved_output_target_is_validated_and_cannot_select_a_missing_monitor(self):
        with patch('app.controller.screen_options', return_value=[{'name':'TV','label':'TV'}]):
            Controller.setOutputTarget(self.h, 'TV', True)
            self.assertEqual(json.loads(self.h.settings_path.read_text())['outputTarget'], {'screenName':'TV','fullScreen':True})
            Controller.setOutputTarget(self.h, 'absent', False)
        self.assertEqual(self.h._state['outputTarget'], {'screenName':'TV','fullScreen':True})
        self.assertIn('已断开', self.h._state['notice'])
        with self.assertRaises(ValueError):
            validate_offsets({str(i):0 for i in range(513)})
        with self.assertRaises(ValueError):
            validate_output_target({'screenName':[], 'fullScreen':True})
