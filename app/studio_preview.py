"""The editor samples real media and paints with our own export renderer."""
import copy
import json
import threading
from urllib.parse import parse_qs
from PySide6.QtGui import QImage,QPainter,QColor
from PySide6.QtQuick import QQuickImageProvider
from worker.studio_render import draw_frame
from worker.composition_render import CompositionSampler
from worker.studio_render import probe_media
from worker.style_catalog import template_settings


class StudioPreviewProvider(QQuickImageProvider):
    def __init__(self, controller):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.controller=controller
        self._sampler=None
        self._signature=''
        self._legacy_sampler=None
        self._legacy_signature=''
        self._legacy_duration=0
        self._template_thumbs={}
        self._lock=threading.RLock()

    def shutdown(self):
        with self._lock:
            self._close_samplers()

    def _close_samplers(self):
        if self._sampler:
            self._sampler.close()
            self._sampler=None
        self._signature=''
        if self._legacy_sampler:
            self._legacy_sampler.close()
            self._legacy_sampler=None
        self._legacy_signature=''

    def _background_frame(self,state):
        identity=copy.deepcopy(self.controller._sources.get('background'))
        signature=json.dumps({'identity':identity,'width':state['settings']['width'],
            'height':state['settings']['height'],'fps':state['settings']['fps']},sort_keys=True)
        if signature!=self._legacy_signature:
            if self._legacy_sampler:self._legacy_sampler.close()
            self._legacy_sampler=None;self._legacy_signature=signature
            if identity and not state.get('backgroundIsImage'):
                from app.composition import empty_composition,make_clip
                info=probe_media(identity['path'],include_video=True)
                video=next((row for row in info['streams'] if row['codec_type']=='video'),None)
                if not video:raise ValueError('背景素材没有视频轨道')
                self._legacy_duration=float(info['format']['duration'])*1000
                value=empty_composition();value['fps']=state['settings']['fps']
                value['sources']['background']={'identity':identity,'kind':'video',
                    'durationMs':self._legacy_duration,'width':video['width'],'height':video['height']}
                value['clips']=[make_clip('background','video',0,self._legacy_duration)]
                self._legacy_sampler=CompositionSampler(value,state['settings']['width'],
                    state['settings']['height'],duration_ms=self._legacy_duration)
        if self._legacy_sampler:return self._legacy_sampler.frame(state.get('positionMs',0)%self._legacy_duration)
        if identity and state.get('backgroundIsImage'):
            background=QImage(identity['path'])
            if background.isNull():raise ValueError('背景图片无法解码')
            return background
        return None

    def requestImage(self,identifier,size,requested_size):
        with self._lock:
            return self._request_image(identifier,size,requested_size)

    def _request_image(self,identifier,size,requested_size):
        if identifier.startswith('template-thumb/'):
            template=identifier.split('/',1)[1].partition('?')[0]
            if template not in self._template_thumbs:
                settings=template_settings(self.controller.state['settings'],template)
                settings.update(width=640,height=360,backgroundColor='#171B22')
                rows=[{'startMs':0,'endMs':5000,'text':'把今晚唱成回忆'}, {'startMs':5500,'endMs':8000,'text':'让每一句都有画面'}]
                if template == 'karaoke':
                    rows[0]['characters']=[{'text':'把今晚','startMs':0,'endMs':2500},
                        {'text':'唱成回忆','startMs':2500,'endMs':5000}]
                self._template_thumbs[template]=draw_frame(rows,settings,1800)
            frame=self._template_thumbs[template]
            if requested_size.isValid():frame=frame.scaled(requested_size)
            size.setWidth(frame.width());size.setHeight(frame.height());return frame
        state=self.controller.state
        # Draw at editor resolution; export retains the project's full output size.
        if requested_size.isValid() and requested_size.width()>0:
            width=min(state['settings']['width'],requested_size.width())
            height=max(16,round(width*state['settings']['height']/state['settings']['width']))
            state['settings'].update(width=max(16,width),height=height)
        composition=copy.deepcopy(state.get('composition',{}))
        query=parse_qs(identifier.partition('?')[2])
        if 'concertRow' in query and state.get('concertRows'):
            if self.controller._concert_draft is not None:composition=copy.deepcopy(self.controller._concert_draft)
        signature=json.dumps({'sources':composition.get('sources',{}),'clips':composition.get('clips',[]),
            'width':state['settings']['width'],'height':state['settings']['height'],
            'fps':state['settings']['fps'],'durationMs':state['durationMs']},sort_keys=True)
        background=None;error=''
        try:
            if signature!=self._signature:
                if self._sampler:self._sampler.close()
                self._sampler=None
                if composition.get('clips'):
                    self._sampler=CompositionSampler(composition,state['settings']['width'],state['settings']['height'],state['settings']['fps'],duration_ms=state['durationMs'])
                self._signature=signature
            position=state.get('positionMs',0)
            active=any(row['startMs']<=position<row['startMs']+row['durationMs'] for row in composition.get('clips',[]))
            background=self._sampler.frame(position) if self._sampler and active else self._background_frame(state)
        except (OSError,ValueError,TypeError,KeyError,RuntimeError) as failure:
            error=str(failure)
            self.shutdown()
        frame=draw_frame(state.get('segments',[]),state['settings'],state.get('positionMs',0),
                         background,composition=composition or None)
        if error:
            painter=QPainter(frame);painter.fillRect(0,0,frame.width(),40,QColor('#c0332233'))
            painter.setPen(QColor('#ffffff'));painter.drawText(12,25,'素材预览不可用：'+error[:90]);painter.end()
        if requested_size.isValid():frame=frame.scaled(requested_size)
        size.setWidth(frame.width());size.setHeight(frame.height())
        return frame
