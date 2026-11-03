"""One sweep switch across styles, shaped wrapped text and studio templates."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_live_controls as controls
from tests.test_live_karaoke import APPLICATION, rgba
from app.controller import Controller
from app.karaoke import sweep_cursor
from app.lyric_paint import GradientLyricItem
from app.lyrics import project_lyrics
from app.studio import StudioController
from app.style_catalog import style_catalog
from app.styles import style_preset
from worker.style_catalog import DEFAULT_SETTINGS, TEMPLATES, normalize_settings
from worker.studio_render import draw_frame
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

ROOT = Path(__file__).resolve().parents[1]


class LyricSweepTests(unittest.TestCase):
    def test_live_switch_persists_and_survives_every_preset_and_layout(self):
        fixture = controls.LiveControlTests()
        fixture.setUp()
        try:
            h = fixture.h
            h._state['style'] = style_preset('minimal')
            Controller.setLyricSweep(h, True)
            self.assertNotEqual(h._state['style']['baseColor'], h._state['style']['highlightColor'])
            for enabled in (True, False):
                Controller.setLyricSweep(h, enabled)
                for preset in style_catalog():
                    Controller.setStylePresetScheme(h, preset['id'], 0)
                    for layout in ('single','double','karaoke'):
                        h.setLiveLayout(layout)
                        self.assertEqual(h._state['style']['sweepMode'], 'word' if enabled else 'sentence')
                self.assertEqual(json.loads(h.settings_path.read_text())['style'], h._state['style'])
            before=copy.deepcopy(h._state['style'])
            with patch('app.controller.os.replace',side_effect=OSError('disk unavailable')):
                Controller.setLyricSweep(h,True)
            self.assertEqual(h._state['style'],before)
            self.assertIn('歌词走带未保存',h._state['notice'])
            Controller.setLyricSweep(h,True)
            self.assertEqual(h._state['notice'],'')
        finally:
            fixture.tearDown()

    def test_nontraditional_sentence_sweep_and_word_gaps_query_source_time(self):
        sentence={'quality':'sentence','segments':[{'text':'灯光沿着节拍亮起','startMs':1000,'endMs':5000}]}
        original=copy.deepcopy(sentence)
        for position,progress in ((1000,0),(3000,.5),(4000,.75),(3000,.5)):
            state=project_lyrics(sentence,position,sweep=True)
            self.assertEqual(state['progress'],progress)
            self.assertEqual(state['quality'],'sentence')
            self.assertEqual(state['sweepCursor'],[0,8,progress])
        self.assertEqual(sentence,original)
        self.assertFalse(project_lyrics(sentence,5000,sweep=True)['visible'])
        self.assertEqual(project_lyrics(sentence,3000)['progress'],0)
        words={'quality':'word','segments':[dict(sentence['segments'][0],words=[
            {'text':'灯光沿着','startMs':1000,'endMs':2000},
            {'text':'节拍亮起','startMs':3000,'endMs':5000}])]}
        self.assertEqual(project_lyrics(words,2200,sweep=True)['sweepCursor'],
                         project_lyrics(words,2800,sweep=True)['sweepCursor'])

    def test_shaped_wrapped_sweep_advances_without_rectangular_fill_or_layout_rebuild(self):
        item=GradientLyricItem()
        item.setWidth(190)
        item.padding=10
        item.text='AVfi你好😀，跟着节奏继续唱，一起把下一行点亮'
        font=QFont('Heiti SC',24);font.setPixelSize(24)
        item.font=font
        item.gradientEnabled=False
        item.outlineWidth=0
        item.shadowEnabled=False
        item.textColor=QColor('#FFFFFF')
        item.sweepColor=QColor('#FFE600')
        item.sweepEnabled=True
        item._rebuild()
        self.assertGreater(item.lineCount,1)
        layout=item._layout
        def render(fraction):
            item.sweepCursor=[0,len(item.text),fraction]
            image=QImage(190,round(item.textHeight+20),QImage.Format.Format_RGBA8888)
            image.fill(Qt.GlobalColor.transparent)
            painter=QPainter(image)
            try:item.paint(painter)
            finally:painter.end()
            pixels=rgba(image)
            yellow=sum(pixels[i]>180 and pixels[i+1]>170 and pixels[i+2]<80 and pixels[i+3]>200
                       for i in range(0,len(pixels),4))
            return pixels,yellow
        zero=render(0);early=render(.25);late=render(.75);full=render(1)
        self.assertEqual(zero[1],0)
        self.assertGreater(early[1],0)
        self.assertGreater(late[1],early[1])
        self.assertGreater(full[1],late[1])
        self.assertEqual(early,render(.25))
        self.assertIs(item._layout,layout)
        for before,after in zip(zero[0][3::4],full[0][3::4]):
            self.assertEqual(bool(before),bool(after))

    def test_all_studio_templates_support_the_switch_and_duplicate_next_line_stays_unsung(self):
        row={'text':'跟着灯光一起唱，下一行也继续走带。','startMs':1000,'endMs':5000}
        for template in TEMPLATES:
            settings=dict(DEFAULT_SETTINGS,templateId=template['id'],fadeMs=0,
                          textColor='#FFFFFF',highlightColor='#FFE600',lyricSweep=True)
            with self.subTest(template=template['id']):
                self.assertNotEqual(rgba(draw_frame([row],settings,2000)),rgba(draw_frame([row],settings,4000)))
                settings['lyricSweep']=False
                self.assertEqual(rgba(draw_frame([row],settings,2000)),rgba(draw_frame([row],settings,4000)))
        following=dict(row,startMs=6000,endMs=9000)
        for template in ('karaoke','karaoke-card','none','split'):
            calls=[]
            def record(*args,**kwargs):calls.append(kwargs.get('highlight_cursor'))
            with patch('worker.studio_render._paint_line',side_effect=record):
                draw_frame([row,following],dict(DEFAULT_SETTINGS,templateId=template,lyricSweep=True,fadeMs=0),3000)
            self.assertIsNotNone(calls[0])
            self.assertIsNone(calls[-1])

    def test_studio_legacy_defaults_boolean_validation_undo_and_template_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            studio=StudioController(ROOT,Path(folder))
            try:
                for template in ('none','karaoke','headline','karaoke-card'):
                    legacy=dict(studio.state['settings'],templateId=template)
                    legacy.pop('lyricSweep')
                    expected=template in ('karaoke','karaoke-card')
                    self.assertEqual(studio._validated_settings(legacy)['lyricSweep'],expected)
                    self.assertEqual(normalize_settings(legacy)['lyricSweep'],expected)
                source=copy.deepcopy(studio.state['segments'])
                studio.setSetting('lyricSweep',False)
                studio.applyTemplate('headline')
                studio.setSetting('lyricSweep',True)
                studio.undo()
                self.assertFalse(studio.state['settings']['lyricSweep'])
                self.assertEqual(studio.state['settings']['templateId'],'headline')
                self.assertEqual(studio.state['segments'],source)
                with self.assertRaises(ValueError):normalize_settings(dict(DEFAULT_SETTINGS,lyricSweep=1))
            finally:
                studio.shutdown();APPLICATION.processEvents()

    def test_studio_project_round_trip_keeps_sweep_setting(self):
        from tests.test_studio_usability import synthesize_audio, wait_idle
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            audio=path/'fixture.wav'
            synthesize_audio(audio)
            studio=StudioController(ROOT,path/'original')
            reloaded=StudioController(ROOT,path/'reloaded')
            try:
                studio.chooseAudio(str(audio));wait_idle(studio)
                studio.applyTemplate('vertical')
                studio.setSetting('lyricSweep',True)
                project=path/'sweep.bfstudio.json'
                studio.saveProject(str(project))
                self.assertTrue(project.exists(),studio.state['status'])
                reloaded.openProject(str(project));wait_idle(reloaded)
                self.assertTrue(reloaded.state['settings']['lyricSweep'])
                self.assertEqual(reloaded.state['settings']['templateId'],'vertical')
                self.assertFalse(reloaded.state['dirty'])
            finally:
                studio.shutdown();reloaded.shutdown();APPLICATION.processEvents()


if __name__=='__main__':unittest.main()
