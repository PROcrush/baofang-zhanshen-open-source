"""Template selection must match its preview, preserve the song and undo once."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from PySide6.QtCore import QSize
from app.studio import StudioController,validate_segments
from app.studio_preview import StudioPreviewProvider
from worker.style_catalog import EFFECTS,TEMPLATES
from worker.studio_render import draw_frame
from tests.test_studio import APPLICATION,ROOT


def digest(image):
    return hashlib.sha256(image.constBits().tobytes()).hexdigest()


class TemplatePresetTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.studio=StudioController(ROOT,Path(self.temporary.name))
        self.studio._state['durationMs']=8000
        self.rows=validate_segments([
            {'startMs':0,'endMs':5000,'text':'把今晚唱成回忆','characters':[
                {'text':'把今晚','startMs':0,'endMs':2500},
                {'text':'唱成回忆','startMs':2500,'endMs':5000}]},
            {'startMs':5500,'endMs':8000,'text':'让每一句都有画面'}],8000)
        self.studio._state['segments']=copy.deepcopy(self.rows)
        self.studio._emit()

    def tearDown(self):
        self.studio.shutdown();APPLICATION.processEvents();self.temporary.cleanup()

    def test_complete_presets_clear_previous_motion_without_changing_song(self):
        self.studio.setSetting('width',1280)
        self.studio.setSetting('textColor','#EEAEBB')
        self.studio.setSetting('effectId','bounce')
        self.studio.setSetting('layoutId','wave-06')
        self.studio.seek(2700)
        before=copy.deepcopy(self.studio._content())
        original=copy.deepcopy(self.studio.state['settings'])
        count=len(self.studio._undo)
        self.studio.applyTemplate('minimal')
        settings=self.studio.state['settings']
        self.assertEqual((settings['fontSize'],settings['fontFamily'],settings['strokeWidth']),(30,'PingFang SC',1.2))
        self.assertEqual((settings['effectId'],settings['layoutId'],settings['layoutParameters']),('none','standard',{}))
        self.assertEqual((settings['width'],settings['height']),(1280,720))
        self.assertEqual(self.studio.state['segments'],self.rows)
        self.assertEqual(self.studio.state['positionMs'],2700)
        self.assertEqual(len(self.studio._undo),count+1)
        self.studio.applyTemplate('minimal');self.assertEqual(len(self.studio._undo),count+1)
        self.studio.undo()
        self.assertEqual(self.studio.state['settings'],original)
        self.assertEqual(self.studio._content(),before)

    def test_gallery_preview_equals_applied_preset_even_after_custom_ktv_colors(self):
        provider=StudioPreviewProvider(self.studio)
        try:
            self.studio.setSetting('textColor','#EEAEBB')
            self.studio.setSetting('strokeWidth',8)
            for template in TEMPLATES:
                with self.subTest(template=template['id']):
                    self.studio.applyTemplate(template['id'])
                    settings=self.studio.state['settings']
                    self.assertEqual(settings['templateId'],template['id'])
                    settings.update(width=640,height=360,backgroundColor='#171B22')
                    rows=copy.deepcopy(self.rows)
                    if template['id']!='karaoke':rows[0].pop('characters')
                    expected=draw_frame(rows,settings,1800)
                    thumb=provider.requestImage('template-thumb/'+template['id'],QSize(),QSize())
                    self.assertEqual(digest(thumb),digest(expected))
                    self.assertEqual(self.studio.state['segments'],self.rows)
        finally:provider.shutdown()

    def test_invalid_template_keeps_current_style_and_one_step_ktv_restores_classic(self):
        self.studio.setSetting('templateId','neon')
        before=self.studio.state['settings']
        self.studio.applyTemplate('not-a-template')
        self.assertEqual(self.studio.state['settings'],before)
        self.studio.setSetting('templateId','karaoke')
        self.assertEqual(self.studio.state['settings']['highlightColor'],'#FFE600')
        self.assertEqual(self.studio.state['settings']['strokeWidth'],3)
        self.assertEqual(self.studio.state['segments'],self.rows)

    def test_selecting_animation_after_static_template_has_visible_frames_and_one_undo(self):
        choices=[('effectId',effect) for effect in ('fade','rise')]
        choices += [(key,effect['id']) for key in ('entranceId','exitId')
                    for effect in EFFECTS if effect['id']!='none']
        for key,effect in choices:
            with self.subTest(key=key,effect=effect):
                self.studio.applyTemplate('headline')
                self.studio.setSetting('lyricSweep',False)
                before=self.studio.state['settings']
                count=len(self.studio._undo)
                self.assertEqual(before['fadeMs'],0)
                self.studio.setSetting(key,effect)
                selected=self.studio.state['settings']
                self.assertGreater(selected['fadeMs'],0)
                position=4950 if key=='exitId' else 50
                plain=dict(selected,**{key:'none'})
                self.assertNotEqual(digest(draw_frame(self.rows,selected,position)),
                                    digest(draw_frame(self.rows,plain,position)))
                self.assertEqual(self.studio.state['segments'],self.rows)
                self.assertEqual(len(self.studio._undo),count+1)
                self.studio.undo()
                self.assertEqual(self.studio.state['settings'],before)

    def test_animation_selection_keeps_nonzero_duration_and_explicit_zero_is_honored(self):
        self.studio.applyTemplate('headline')
        self.studio.setSetting('fadeMs',650)
        self.studio.setSetting('effectId','rise')
        self.assertEqual(self.studio.state['settings']['fadeMs'],650)
        self.studio.setSetting('fadeMs',0)
        self.assertEqual(self.studio.state['settings']['fadeMs'],0)
        self.studio.setSetting('effectId','none')
        self.assertEqual(self.studio.state['settings']['fadeMs'],0)


if __name__=='__main__':unittest.main()
