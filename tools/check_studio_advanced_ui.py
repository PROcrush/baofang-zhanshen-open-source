"""Exercise independent authoring through actual QML controls on generated fixtures."""
import json
import math
import os
import struct
import subprocess
import time
import uuid
import wave
import shiboken6
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.studio import StudioController
from app.studio_preview import StudioPreviewProvider
from worker.studio_render import find_ffmpeg
from tools.ui_state_smoke import SmokeController, click, named_items, visual_items


def main():
    root = Path(__file__).resolve().parent.parent
    target = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root / 'runtime/ui-humanized-20261012/advanced'
    target.mkdir(parents=True, exist_ok=True)
    fixtures = target / 'fixtures' / uuid.uuid4().hex
    fixtures.mkdir(parents=True)
    application = QApplication.instance() or QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    audio, video, image = fixtures/'合成节拍.wav', fixtures/'合成演唱会.mp4', fixtures/'原创补镜.png'
    rate = 48000
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(rate)
        frames = bytearray()
        for index in range(rate*8):
            seconds = index/rate
            envelope = math.exp(-(seconds % .5)*35)
            frames.extend(struct.pack('<h', int(7000*envelope*math.sin(2*math.pi*160*seconds))))
        stream.writeframes(frames)
    art = QImage(256,144,QImage.Format.Format_ARGB32)
    art.fill(QColor('#426784')); assert art.save(str(image))
    ffmpeg = str(find_ffmpeg())
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                    'testsrc2=size=256x144:rate=12:duration=8','-f','lavfi','-i',
                    'anullsrc=r=48000:cl=mono','-shortest','-c:v','libx264','-pix_fmt','yuv420p',
                    '-c:a','aac',str(video)],check=True,timeout=60)
    rows = [{'start':.3,'end':1.3,'text':'灯光','characters':[
                {'text':'灯','start':.3,'end':.8},{'text':'光','start':.8,'end':1.3}]},
            {'start':1.6,'end':3.5,'text':'声音靠近'},
            {'start':3.8,'end':5.7,'text':'夜晚有自己的颜色'},
            {'start':6.0,'end':7.7,'text':'把快乐留在这里'}]
    subtitles = fixtures/'合成歌词.json'
    subtitles.write_text(json.dumps({'segments':rows},ensure_ascii=False))
    concert_subtitles = fixtures/'来源歌词.json'
    concert_subtitles.write_text(json.dumps({'segments':rows},ensure_ascii=False))
    studio = StudioController(root, target/'runtime')
    assert callable(getattr(studio,'chooseMaterial',None)), 'Advanced StudioController contract is not installed yet'
    warnings, screenshots, checks = [], [], []
    report = None
    engine = QQmlApplicationEngine()
    facade = SmokeController('replay')
    engine.rootContext().setContextProperty('controller',facade)
    engine.rootContext().setContextProperty('studio',studio)
    provider = StudioPreviewProvider(studio)
    engine.addImageProvider('studioPreview',provider)
    engine.warnings.connect(lambda items:warnings.extend(item.toString() for item in items))
    window = None

    def wait_idle(timeout=120):
        deadline = time.monotonic()+timeout
        while studio.state['busy'] and time.monotonic()<deadline:
            QTest.qWait(20)
        QTest.qWait(40)
        assert not studio.state['busy'],studio.state['status']

    def item(name):
        found = named_items(window).get(name)
        assert found is not None, 'Missing QML object: '+name
        return found

    def reveal(control):
        # Scroll an actual nested inspector/list so hit testing reaches the target.
        ancestors, node = [], control.parentItem()
        while node:
            ancestors.append(node); node=node.parentItem()
        for ancestor in ancestors:
            if ancestor.property('contentY') is None or ancestor.property('contentHeight') is None:
                continue
            content = ancestor.property('contentItem')
            if content is None:
                continue
            top = control.mapToItem(content,QPointF(0,0)).y()
            height = ancestor.height()
            position = float(ancestor.property('contentY'))
            if top < position+4 or top+control.height()>position+height-4:
                ancestor.setProperty('contentY',max(0,min(float(ancestor.property('contentHeight'))-height,top-8)))
        QTest.qWait(25)

    def tap(name):
        control=item(name); reveal(control)
        assert control.isVisible() and control.isEnabled(), 'QML action unavailable: '+name
        click(window,control); QTest.qWait(40)
        return control

    def text_button(text):
        for node in visual_items(window.contentItem()):
            if node.isVisible() and node.property('text')==text and 'StudioButton' in node.metaObject().className():
                reveal(node); click(window,node); QTest.qWait(30); return
        raise AssertionError('Missing visible button: '+text)

    def enter(name,value):
        control=tap(name)
        control.setProperty('text',str(value))
        QTest.keyClick(window,Qt.Key.Key_Return); QTest.qWait(40)

    def choose(control_name,path):
        with patch('app.studio.QFileDialog.getOpenFileName',return_value=(str(path),'')):
            tap(control_name)
        wait_idle()

    def catalog(name,identifier):
        tap(name)
        edit=item(name+'Search'); click(window,edit); edit.setProperty('text',identifier)
        QTest.qWait(70)
        tap(name+'Option-'+identifier)

    def select(kind,identifier):
        # Click the real track clip, including vertically scrolled text/audio tracks.
        tap('timeline-'+kind+'-'+identifier)

    try:
        studio.chooseAudio(str(audio)); wait_idle()
        studio.chooseSubtitles(str(subtitles)); wait_idle()
        engine.load(QUrl.fromLocalFile(str(root/'ui/Main.qml')))
        assert engine.rootObjects(), '\n'.join(warnings)
        window=engine.rootObjects()[0]; window.setProperty('currentPage',5); QTest.qWait(100)
        workspace=item('studioWorkspace')
        workspace.setProperty('advancedEditing',True)
        QTest.qWait(100)
        text_button('歌曲素材')
        choose('studioChooseMaterial',video)
        source=studio.state['materialSources'][-1]; source_id=source.get('sourceId',source.get('id'))
        tap('studioMaterial-'+source_id); tap('studioAddSelectedClip')
        assert len(studio.state['composition']['clips'])==1,studio.state['status']
        clip_id=studio.state['composition']['clips'][0]['id']
        enter('compositionDuration','4.000')
        assert studio.state['composition']['clips'][0]['durationMs']==4000
        studio.seek(2000); QTest.qWait(40); tap('compositionSplit')
        assert len(studio.state['composition']['clips'])==2,studio.state['status']
        second=studio.state['composition']['clips'][1]['id']; select('clip',second)
        enter('compositionRate','0.500')
        assert studio.state['composition']['clips'][1]['rate']==.5,studio.state['status']
        catalog('compositionTransition','fade-left')
        assert studio.state['composition']['clips'][1]['transition']['id']=='fade-left',studio.state['status']
        tap('compositionLocked')
        assert studio.state['composition']['clips'][1]['locked']
        assert not item('compositionDuration').isEnabled()
        tap('compositionLocked')
        studio.seek(4000); choose('studioChooseMaterial',image)
        source=studio.state['materialSources'][-1]; source_id=source.get('sourceId',source.get('id'))
        tap('studioMaterial-'+source_id); tap('studioAddSelectedClip')
        assert len(studio.state['composition']['clips'])==3,studio.state['status']
        third_id=studio.state['composition']['clips'][-1]['id']
        enter('compositionDuration','2.000')
        assert studio.state['composition']['clips'][-1]['durationMs']==2000,studio.state['status']
        def drag_clip(identifier,pixels,edge=False):
            control=item('timeline-clip-'+identifier); reveal(control)
            local=QPointF(control.width()-3 if edge else control.width()/2,control.height()/2)
            origin=control.mapToScene(local).toPoint()
            press=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(origin),QPointF(window.mapToGlobal(origin)),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            application.sendEvent(window,press)
            QTest.qWait(10)
            if not edge: assert item('clipMoveArea-'+identifier).property('pressed'),'Clip MouseArea did not receive press'
            destination=origin+QPoint(pixels,0)
            motion=QMouseEvent(QEvent.Type.MouseMove,QPointF(destination),QPointF(window.mapToGlobal(destination)),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            application.sendEvent(window,motion); QTest.qWait(40)
            release=QMouseEvent(QEvent.Type.MouseButtonRelease,QPointF(destination),QPointF(window.mapToGlobal(destination)),Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
            application.sendEvent(window,release)
            QTest.qWait(70)
        drag_clip(third_id,24)
        third=next(row for row in studio.state['composition']['clips'] if row['id']==third_id)
        assert third['startMs']>4000,studio.state['status']
        assert abs(third['startMs']/(1000/studio.state['settings']['fps'])-round(third['startMs']/(1000/studio.state['settings']['fps'])))<.0001
        drag_clip(third_id,15,edge=True)
        third=next(row for row in studio.state['composition']['clips'] if row['id']==third_id)
        assert third['durationMs']>2000,studio.state['status']
        before_rejected=dict(third)
        drag_clip(third_id,-300)
        third=next(row for row in studio.state['composition']['clips'] if row['id']==third_id)
        assert third==before_rejected,'Rejected overlap did not restore original model/visual binding'
        checks.append('real QML clip drag and right trim snap to frames; overlap rejection restores original clip')
        checks.append('QML material import, clip trim/rate/split/transition/lock and three shot timeline')

        tap('studioAddText')
        text_id=studio.state['composition']['texts'][-1]['id']
        text_editor=tap('compositionTextEditor'); text_editor.setProperty('text','包房战神原创验收')
        tap('compositionTextApply')
        assert studio.state['composition']['texts'][-1]['text']=='包房战神原创验收'
        text_button('撤销')
        assert item('compositionTextEditor').property('text')==studio.state['composition']['texts'][-1]['text']
        text_editor=item('compositionTextEditor'); text_editor.setProperty('text','包房战神独立制作'); tap('compositionTextApply')
        enter('compositionTextX','35.0'); enter('compositionTextY','16.0')
        assert studio.state['composition']['texts'][-1]['x']==.35
        png=fixtures/'独立文字.png'
        with patch('app.studio.QFileDialog.getSaveFileName',return_value=(str(png),'')):
            tap('compositionTextPNG')
        wait_idle(); assert png.is_file(),studio.state['status']
        choose('studioChooseMaterial',audio)
        source=studio.state['materialSources'][-1]; source_id=source.get('sourceId',source.get('id'))
        tap('studioMaterial-'+source_id); tap('studioAddSelectedAudio')
        enter('compositionAudioGain','50')
        assert studio.state['composition']['audioClips'][-1]['gain']==.5
        tap('compositionAudioDelete'); assert not studio.state['composition']['audioClips']
        text_button('撤销'); assert studio.state['composition']['audioClips']
        checks.append('QML independent text, undo field sync, position, PNG and actual additive audio gain/removal')

        text_button('歌词'); tap('studioLyricRow-1'); text_button('模板')
        tap('templateTile-headline'); tap('styleAdvancedToggle'); catalog('styleEffect','fade'); catalog('styleLayout','wave-06')
        enter('styleLayout-amplitude','.230')
        assert studio.state['settings']['layoutParameters'].get('amplitude')==.23,studio.state['status']
        text_button('时码'); enter('lyricTimingStart','1.700'); enter('lyricTimingEnd','3.400'); tap('lyricTimingApply')
        assert studio.state['segments'][1]['startMs']==1700
        enter('lyricSplitTime','2.500'); enter('lyricSplitChar','2'); tap('lyricSplit')
        assert len(studio.state['segments'])==5,studio.state['status']
        tap('lyricMerge'); assert len(studio.state['segments'])==4
        tap('studioLyricRow-0'); enter('lyricCharEnd-0','.700'); tap('lyricCharApply-0')
        assert studio.state['segments'][0]['characters'][0]['endMs']==700,studio.state['status']
        tap('studioOutputSettings')
        output_srt=fixtures/'导出歌词.srt'
        with patch('app.studio.QFileDialog.getSaveFileName',return_value=(str(output_srt),'')):
            text_button('SRT')
        assert output_srt.is_file(),studio.state['status']
        checks.append('QML searchable template/effect/layout, real parameter override, lyric sentence/character timing, split/merge and SRT export')

        tap('studioAnalyzeAudio'); wait_idle()
        assert studio.state['waveform'],studio.state['analysisStatus']
        tap('studioAddMarker'); assert studio.state['composition']['markers']
        checks.append('actual waveform and beat analysis with QML marker addition')

        text_button('演唱会'); choose('concertChooseVideo',video); tap('concertAnalyzeMotion'); wait_idle(); assert studio.state['concertAnalysis']['sampledFrames']>0; choose('concertChooseLyrics',concert_subtitles)
        tap('concertGeneratePlan'); wait_idle()
        assert studio.state['concertRows'],studio.state['concertStatus']
        paired=next(index for index,row in enumerate(studio.state['concertRows']) if row['purpose']=='paired')
        tap('concertCandidate-'+str(paired))
        enter('concertRate','.800'); tap('concertRowReviewed')
        assert studio.state['concertRows'][paired]['reviewed']
        assert not item('concertApplyPlan').isEnabled()
        tap('concertAllReviewed'); tap('concertApplyPlan')
        assert studio.state['composition']['concert'].get('reviewed'),studio.state['status']
        assert studio.state['concertCoverage']['coverageRatio']==1,studio.state['concertCoverage']
        checks.append('QML concert source import, candidate review/rate edit, explicit whole-plan confirmation and apply with full candidate coverage')

        text_button('歌曲素材'); tap('studioAutoStoryboard')
        assert len(studio.state['composition']['clips'])>=3,studio.state['status']
        studio.seek(4200); tap('studioOutputSettings')
        format_control=tap('studioOutputFormat'); QTest.keyClick(window,Qt.Key.Key_End); QTest.keyClick(window,Qt.Key.Key_Return); QTest.qWait(40)
        assert studio.state['outputFormat']=='mov-alpha'
        alpha=fixtures/'透明字幕.mov'
        with patch('app.studio.QFileDialog.getSaveFileName',return_value=(str(alpha),'')):
            text_button('导出视频'); tap('studioGenerateVideo')
        wait_idle(180); assert Path(studio.state['resultPath']).suffix=='.mov' and Path(studio.state['resultPath']).is_file(),studio.state['status']
        alpha_result=studio.state['resultPath']
        studio.setOutputFormat('mp4')
        text_button('生成预览'); wait_idle(180)
        assert studio.state['resultPath'] and Path(studio.state['resultPath']).is_file(),studio.state['status']
        workspace.setProperty('showRendered',False)
        text_button('歌词'); tap('studioLyricRow-2'); text_button('模板'); studio.seek(4200)
        for node in visual_items(window.contentItem()):
            if node.isVisible() and node.property('contentY') is not None and node.property('contentHeight') is not None:
                node.setProperty('contentY',0)
        project=target/'高级制作示例.bfstudio.json'
        if project.exists(): project=target/('高级制作示例-'+fixtures.name[:8]+'.bfstudio.json')
        studio.saveProject(str(project)); assert project.is_file() and studio.state['projectPath']==str(project),studio.state['status']
        assert json.loads(project.read_text())['version']==3
        for width,height in [(1000,700),(1440,900)]:
            window.resize(width,height); QTest.mouseMove(window,QPoint(width-15,height-15)); QTest.qWait(150)
            filename=target/f'{width}-advanced.png'; assert window.grabWindow().save(str(filename)); screenshots.append(str(filename))
        checks.append('QML auto storyboard, actual transparent MOV and independent MP4 export, and 1000/1440 layout')
        assert not warnings,'\n'.join(warnings)
        report={'passed':True,'syntheticMedia':True,'cloudRequests':0,'checks':checks,'qmlWarnings':warnings,'screenshots':screenshots,'resultPath':studio.state['resultPath'],'alphaResultPath':alpha_result,'projectPath':str(project)}

    finally:
        if window is not None:
            window.setProperty('currentPage',0); QTest.qWait(30)
            window.close(); shiboken6.delete(window)
        shiboken6.delete(engine)
        application.processEvents()
        studio.shutdown()
    if report is not None:
        assert not warnings,'\n'.join(warnings)
        (target/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
