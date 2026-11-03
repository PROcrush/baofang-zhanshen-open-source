"""Novice journey through real QML controls, local audio and actual MP4 export."""
import json
import os
import shutil
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QAccessible, QAccessibleActionInterface, QColor, QImage, QKeySequence
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from app.studio import StudioController
from app.studio_preview import StudioPreviewProvider
from tests.test_studio import APPLICATION, ROOT, wait_idle
from tools.ui_state_smoke import SmokeController, named_items, click


def main():
    target = Path(os.environ.get('BAOFANG_UI_CHECK_DIR', str(ROOT/'runtime/ui-simple-20261015')))
    target.mkdir(parents=True, exist_ok=True)
    screenshots = []
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory).resolve()
        audio = folder/'简单上手演示.wav'
        with wave.open(str(audio),'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(48000)
            stream.writeframes(b'\0\0'*48000*8)
        subtitle = folder/'歌词.json'
        rows = [{'startMs':100,'endMs':3000,'text':'第一句同步歌词','characters':[
            {'text':'第一句','startMs':100,'endMs':1400},
            {'text':'同步歌词','startMs':1600,'endMs':3000}]},
            {'startMs':4000,'endMs':7800,'text':'第二句跟唱歌词','characters':[
            {'text':'第二句','startMs':4000,'endMs':5500},
            {'text':'跟唱歌词','startMs':6000,'endMs':7800}]}]
        subtitle.write_text(json.dumps({'segments':rows},ensure_ascii=False))
        picture = folder/'蓝色背景.png'
        art = QImage(320,180,QImage.Format.Format_RGBA8888)
        art.fill(QColor('#245175')); assert art.save(str(picture))
        studio = StudioController(ROOT,folder/'runtime')
        provider = StudioPreviewProvider(studio)
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda messages:warnings.extend(message.toString() for message in messages))
        facade = SmokeController('replay')
        engine.rootContext().setContextProperty('controller',facade)
        engine.rootContext().setContextProperty('studio',studio)
        engine.addImageProvider('studioPreview',provider)
        QQuickStyle.setStyle('Basic')
        engine.load(QUrl.fromLocalFile(str(ROOT/'ui/Main.qml')))
        assert engine.rootObjects(),warnings
        window = engine.rootObjects()[0]
        window.setProperty('currentPage',5)

        def item(name):
            result = named_items(window).get(name)
            assert result,'Missing '+name
            return result
        def tap(name):
            node=item(name)
            assert node.isVisible() and node.isEnabled(),name
            click(window,node); QTest.qWait(60)
        def shot(name):
            QTest.mouseMove(window,QPoint(window.width()-3,window.height()-3)); QTest.qWait(220)
            path=target/name
            assert window.grabWindow().save(str(path))
            screenshots.append(str(path))
        def visible_inside(name):
            node=item(name); point=node.mapToScene(QPointF(0,0))
            assert node.isVisible() and min(point.x(),point.y())>=0,name
            assert point.x()+node.width()<=window.width()+1 and point.y()+node.height()<=window.height()+1,name+' clipped'

        try:
            QTest.qWait(180)
            assert not item('studioWorkspace').property('advancedEditing')
            for width,height in [(1440,900),(1000,700)]:
                window.resize(width,height); QTest.qWait(100)
                for name in ('simpleStartAudio','simpleWelcomeOpen','simpleMoreEditing','appSettingsMenu'):
                    visible_inside(name)
                assert not item('studioTimeline').isVisible()
                assert not item('studioInspector').isVisible()
                shot(f'empty-{width}.png')
            tap('appSettingsMenu')
            visible_inside('nav-7'); shot('settings-menu-1000.png')
            tap('nav-7'); assert window.property('currentPage')==7
            tap('nav-5')
            tap('simpleMoreEditing'); assert item('studioWorkspace').property('advancedEditing')
            tap('studioEmptyReturnSimple'); assert not item('studioWorkspace').property('advancedEditing')
            with patch('app.studio.QFileDialog.getOpenFileName',return_value=(str(audio),'')):
                tap('simpleStartAudio')
            wait_idle(studio); QTest.qWait(150)
            assert studio.state['settings']['templateId']=='karaoke'
            assert studio.state['settings']['highlightColor']=='#FFE600'
            assert not item('simpleExportVideo').isEnabled()
            tap('simpleAutoRecognize'); assert window.property('currentPage')==7
            tap('nav-5')
            shot('song-ready-1000.png')
            with patch('app.studio.QFileDialog.getOpenFileName',return_value=(str(subtitle),'')):
                tap('simpleAddLyrics')
            wait_idle(studio); QTest.qWait(250)
            assert item('simpleExportVideo').isEnabled()
            studio.applyTemplate('headline')
            QTest.qWait(70)
            for width,height in [(1440,900),(1000,700)]:
                window.resize(width,height);QTest.qWait(80)
                visible_inside('simpleLyricSweep')
                tap('simpleLyricSweep')
                assert not studio.state['settings']['lyricSweep']
                tap('simpleLyricSweep')
                assert studio.state['settings']['lyricSweep']
                assert studio.state['settings']['templateId']=='headline'
            window.requestActivate();QTest.qWait(50)
            modifiers=(Qt.KeyboardModifier.MetaModifier if __import__('sys').platform=='darwin' else Qt.KeyboardModifier.ControlModifier)|Qt.KeyboardModifier.AltModifier
            QTest.keyClick(window,Qt.Key.Key_K,modifiers);QTest.qWait(60)
            assert not studio.state['settings']['lyricSweep']
            QTest.keyClick(window,Qt.Key.Key_K,modifiers);QTest.qWait(60)
            assert studio.state['settings']['lyricSweep']
            action=QAccessible.queryAccessibleInterface(item('simpleLyricSweep')).actionInterface()
            for enabled in (False,True):
                action.doAction(QAccessibleActionInterface.toggleAction()); QTest.qWait(60)
                assert studio.state['settings']['lyricSweep']==enabled
                assert item('simpleLyricSweep').property('checked')==enabled
            studio.applyTemplate('karaoke')
            QTest.qWait(60)
            assert not item('studioTimeline').isVisible()
            assert not item('styleLayout-amplitude').isVisible()
            original=studio._content_key()
            for width,height in [(1440,900),(1000,700)]:
                window.resize(width,height); QTest.qWait(100)
                visible_inside('simpleEffectsButton'); visible_inside('simpleBrowseStyles')
                assert item('simpleEffectsButton').property('text')=='字幕特效'
                tap('simpleEffectsButton')
                assert not item('studioWorkspace').property('advancedEditing')
                tiles=[name for name in named_items(window) if name.startswith('simple-templateTile-')]
                assert len(tiles)==16,tiles
                tap('simpleEffectsAnimations')
                for effect in studio.state['effects']:
                    name='simpleEffect-'+effect['id'];visible_inside(name)
                    before=studio.state['settings']
                    tap(name)
                    assert studio.state['settings']['effectId']==effect['id']
                    assert item(name).property('selected')
                    if studio.state['settings']!=before:
                        studio.undo();QTest.qWait(40)
                shot(f'effects-{width}.png')
                tap('simpleReturnLyrics')
            assert studio._content_key()==original,'Opening effects or undoing selections changed content'
            for width,height in [(1440,900),(1000,700)]:
                window.resize(width,height); QTest.qWait(100)
                for name in ('simplePlay','simplePosition','simpleExportVideo','simpleFontSize','simpleQuality','simpleLyricList'):
                    visible_inside(name)
                assert item('simplePreviewImage').width()>window.width()*.55
                shot(f'editor-{width}.png')
            project=folder/'排练工程.bfstudio.json'
            with patch('app.studio.QFileDialog.getSaveFileName',return_value=(str(project),'')):
                tap('simpleSaveProject')
            assert not studio.state['dirty']
            studio.seek(200)
            tap('simplePlay'); QTest.qWait(450)
            assert studio.state['playbackPlaying'] and studio.state['positionMs']>500
            assert not studio.state['dirty']
            QTest.keyClick(window,Qt.Key.Key_Space); QTest.qWait(80)
            assert not studio.state['playbackPlaying'],'Space did not pause the simple player'
            paused=studio.state['positionMs']; QTest.qWait(150)
            assert studio.state['positionMs']==paused
            studio.seek(4500); QTest.qWait(50)
            assert item('studioSimpleWorkspace').property('selectedLine')==1
            tap('simpleLyric-1'); assert 4000<=studio.state['positionMs']<4400
            tap('simpleEditLyrics')
            editor=item('simpleLyricEditor'); editor.setProperty('text','第二句同步歌词')
            before=[(token['startMs'],token['endMs']) for token in studio.state['segments'][1]['characters']]
            tap('simpleApplyLyric')
            assert studio.state['segments'][1]['text']=='第二句同步歌词'
            assert [(token['startMs'],token['endMs']) for token in studio.state['segments'][1]['characters']]==before
            with patch('app.studio.QFileDialog.getSaveFileName') as dialog:
                tap('simpleSaveProject'); dialog.assert_not_called()
            assert not studio.state['dirty']
            tap('simpleEditLyrics')
            editor.setProperty('text','第二句正式歌词')
            assert item('simpleSaveProject').property('text')=='保存进度'
            tap('simpleSaveProject')
            assert json.loads(project.read_text())['segments'][1]['text']=='第二句正式歌词'
            assert item('studioSimpleWorkspace').property('editingLine')==-1
            # Standard keyboard saves must include the draft even while the editor has focus.
            tap('simpleEditLyrics'); editor.setProperty('text','第二句复核歌词')
            QTest.keySequence(window,QKeySequence(QKeySequence.StandardKey.Save)); QTest.qWait(80)
            assert json.loads(project.read_text())['segments'][1]['text']=='第二句复核歌词'
            previous=project.read_bytes()
            copy=folder/'另存工程.bfstudio.json'
            tap('simpleEditLyrics'); editor.setProperty('text','第二句另存歌词')
            with patch('app.studio.QFileDialog.getSaveFileName',return_value=(str(copy),'')):
                QTest.keySequence(window,QKeySequence(QKeySequence.StandardKey.SaveAs)); QTest.qWait(80)
            assert copy.is_file() and json.loads(copy.read_text())['segments'][1]['text']=='第二句另存歌词'
            assert project.read_bytes()==previous
            project=copy
            before_invalid=project.read_bytes()
            tap('simpleEditLyrics'); editor.setProperty('text','字'*10001)
            tap('simpleSaveProject')
            assert item('studioSimpleWorkspace').property('editingLine')==1
            assert editor.property('text')=='字'*10001,'Validation failure hid the draft'
            assert project.read_bytes()==before_invalid
            editor.setProperty('text','')
            with patch('app.studio.QFileDialog.getSaveFileName') as dialog:
                tap('simpleExportVideo'); dialog.assert_not_called()
            assert item('studioSimpleWorkspace').property('editingLine')==1
            editor.setProperty('text','第二句另存歌词'); tap('simpleSaveProject')
            content=studio._content_key()
            tap('simpleMoreEditing')
            assert item('studioTimeline').isVisible()
            visible_inside('studioLyricSweep')
            tap('studioLyricSweep')
            assert not studio.state['settings']['lyricSweep']
            studio.undo(); QTest.qWait(60)
            assert studio.state['settings']['lyricSweep']
            action=QAccessible.queryAccessibleInterface(item('studioLyricSweep')).actionInterface()
            action.doAction(QAccessibleActionInterface.toggleAction()); QTest.qWait(60)
            assert not studio.state['settings']['lyricSweep']
            studio.undo(); QTest.qWait(60)
            workspace=item('studioWorkspace')
            workspace.setProperty('selectedObject','lyrics'); workspace.setProperty('inspectorPage',2)
            studio.applyTemplate('headline'); QTest.qWait(80)
            assert studio.state['settings']['fadeMs']==0
            tap('styleAdvancedToggle')
            visible_inside('studioEffectsButton');tap('studioEffectsButton')
            assert workspace.property('selectedObject')=='lyrics' and workspace.property('inspectorPage')==2
            assert not item('styleLayout-amplitude').isVisible()
            assert item('styleEffect').isVisible()
            assert len([name for name in named_items(window) if name.startswith('templateTile-')])==16
            tap('styleEffect')
            item('styleEffectSearch').setProperty('text','fade'); QTest.qWait(80)
            tap('styleEffectOption-fade')
            assert studio.state['settings']['effectId']=='fade'
            assert studio.state['settings']['fadeMs']==250
            studio.undo(); studio.undo(); QTest.qWait(80)
            workspace.setProperty('inspectorPage',0)
            tap('studioReturnSimple')
            assert not item('studioTimeline').isVisible()
            assert studio._content_key()==content,'Mode switch changed the project'
            tap('simpleMoreEditing')
            workspace=item('studioWorkspace')
            workspace.setProperty('selectedSegment',1); workspace.setProperty('selectedObject','lyrics'); workspace.setProperty('inspectorPage',0)
            QTest.qWait(80)
            item('studioLyricEditor').setProperty('text','第二句高级保存')
            assert item('studioSaveStatus').property('text')=='未保存'
            tap('studioSaveProject')
            assert json.loads(project.read_text())['segments'][1]['text']=='第二句高级保存'
            item('studioLyricEditor').setProperty('text','第二句高级导出')
            workspace.setProperty('selectedObject','export'); QTest.qWait(80)
            with patch('app.studio.QFileDialog.getSaveFileName',return_value=('','')) as dialog:
                tap('studioGenerateVideo'); dialog.assert_called_once()
            assert studio.state['segments'][1]['text']=='第二句高级导出'
            tap('studioSaveProject'); tap('studioReturnSimple')
            tap('simpleStyleCard'); assert studio.state['settings']['templateId']=='karaoke-card'
            tap('simpleStyleClassic'); assert studio.state['settings']['templateId']=='karaoke'
            before_templates=studio.state['segments']
            tap('simpleBrowseStyles')
            for width,height in [(1440,900),(1000,700)]:
                window.resize(width,height); QTest.qWait(100)
                visible_inside('simpleReturnLyrics'); visible_inside('simple-templateTile-headline')
                shot(f'templates-{width}.png')
            tap('simple-templateTile-headline')
            assert studio.state['settings']['templateId']=='headline'
            assert studio.state['settings']['strokeWidth']==.8
            assert studio.state['settings']['fontFamily']=='PingFang SC'
            assert studio.state['segments']==before_templates
            tap('simple-templateCategory-清爽')
            assert item('simple-templateTile-minimal').isVisible()
            tap('simple-templateTile-minimal')
            assert studio.state['settings']['fontSize']==30
            tap('simple-templateCategory-跟唱')
            tap('simple-templateTile-karaoke')
            assert studio.state['settings']['highlightColor']=='#FFE600'
            tap('simpleReturnLyrics')
            assert item('simpleLyricList').isVisible()
            tap('simpleFontSize'); QTest.keyClick(window,Qt.Key.Key_End); QTest.keyClick(window,Qt.Key.Key_Return); QTest.qWait(50)
            assert studio.state['settings']['fontSize']==56
            tap('simpleQuality'); QTest.keyClick(window,Qt.Key.Key_Down); QTest.keyClick(window,Qt.Key.Key_Return); QTest.qWait(50)
            assert studio.state['settings']['width']==1280
            tap('simpleQuality'); QTest.keyClick(window,Qt.Key.Key_Home); QTest.keyClick(window,Qt.Key.Key_Return); QTest.qWait(50)
            assert studio.state['settings']['width']==854
            with patch('app.studio.QFileDialog.getOpenFileName',return_value=(str(picture),'')):
                tap('simpleBackground')
            wait_idle(studio)
            assert studio.state['backgroundPath']==str(picture),studio.state['status']
            with patch('app.studio.QFileDialog.getSaveFileName',return_value=('','')):
                tap('simpleExportVideo')
            assert not studio.state['busy'] and not studio.state['resultPath']
            output=folder/'我的歌词视频.mp4'
            tap('simpleEditLyrics'); item('simpleLyricEditor').setProperty('text','第二句导出歌词')
            with patch('app.studio.QFileDialog.getSaveFileName',return_value=(str(output),'')):
                tap('simpleExportVideo')
            wait_idle(studio); QTest.qWait(400)
            assert output.is_file() and studio.state['savedVideoPath']==str(output),studio.state['status']
            request=json.loads((studio._job_dir/'render-request.json').read_text())
            assert request['segments'][1]['text']=='第二句导出歌词','Export omitted the visible draft'
            simple_item=item('studioSimpleWorkspace')
            assert QQmlExpression(engine.rootContext(),simple_item,'movie !== null').evaluate()[0]
            assert QQmlExpression(engine.rootContext(),item('studioWorkspace'),'renderedPlayer === null').evaluate()[0], 'Hidden advanced player was also activated'
            tap('simplePlay'); QTest.qWait(450)
            assert QQmlExpression(engine.rootContext(),simple_item,'playing').evaluate()[0], 'Exported video did not play'
            assert QQmlExpression(engine.rootContext(),simple_item,'movie.hasVideo && movie.position > 4400').evaluate()[0]
            sink=QQmlExpression(engine.rootContext(),simple_item,'movie.videoOutput.videoSink').evaluate()[0]
            video_frame=sink.videoFrame()
            assert video_frame.isValid(),'Export player has no decoded video frame'
            decoded=video_frame.toImage()
            assert not decoded.isNull() and decoded.width()==854
            assert decoded.pixelColor(427,100).blue()>80,'Export lost the chosen background'
            assert decoded.save(str(target/'decoded-result.png'))
            tap('simplePlay')
            shot('saved-1000.png')
            tap('simplePreviewToggle'); QTest.qWait(200)
            frame=item('simplePreviewImage')
            for attempt in range(30):
                status=QQmlExpression(engine.rootContext(),frame,'Number(status)').evaluate()[0]
                if status==1:break
                QTest.qWait(100)
            shot('return-to-edit-1000.png')
            assert status==1, (status,str(frame.property('source')),warnings)
            exported=output.read_bytes()
            studio.setSetting('fontSize',48); QTest.qWait(80)
            assert not studio.state['resultUrl'] and not studio.state['savedVideoCurrent']
            assert item('simpleViewSavedVideo').property('text')=='查看上次视频'
            # Open the last result while source audio is running as well as when paused.
            tap('simplePlay'); QTest.qWait(100)
            tap('simpleViewSavedVideo'); QTest.qWait(250)
            assert not studio.state['playbackPlaying']
            assert simple_item.property('showResult')
            assert QQmlExpression(engine.rootContext(),simple_item,'movie !== null && movie.hasVideo').evaluate()[0]
            visible_inside('simplePreviewToggle')
            shot('previous-export-1000.png')
            tap('simplePlay'); QTest.qWait(250)
            assert QQmlExpression(engine.rootContext(),simple_item,'playing').evaluate()[0]
            previous_sink=QQmlExpression(engine.rootContext(),simple_item,'movie.videoOutput.videoSink').evaluate()[0]
            assert previous_sink.videoFrame().isValid(),'Previous export has no decoded video frame'
            tap('simplePlay'); tap('simplePreviewToggle')
            assert item('simplePreviewImage').isVisible()
            assert output.read_bytes()==exported
            tap('simpleSaveProject'); previous=project.read_bytes()
            previous_id=json.loads(previous)['projectId']
            tap('simpleEditLyrics'); item('simpleLyricEditor').setProperty('text','下一首前保留此句')
            other=folder/'下一首.wav'; other.write_bytes(audio.read_bytes())
            with patch('app.studio.QFileDialog.getOpenFileName',return_value=(str(other),'')):
                tap('simpleChangeAudio')
            wait_idle(studio); QTest.qWait(100)
            assert studio.state['projectPath']=='' and not studio.state['segments']
            assert studio._project_id!=previous_id
            assert json.loads(Path(studio.state['recoveryPath']).read_text())['segments'][1]['text']=='下一首前保留此句'
            next_project=folder/'下一首工程.bfstudio.json'
            with patch('app.studio.QFileDialog.getSaveFileName',return_value=(str(next_project),'')) as dialog:
                tap('simpleSaveProject'); dialog.assert_called_once()
            assert project.read_bytes()==previous
            assert next_project.is_file()
            shot('next-song-1000.png')
            assert not warnings,warnings
            report={'passed':True,'checks':['default simple UI','traditional KTV defaults','settings menu navigation',
                'song import','recognition entry without a cloud request','lyric import and editing','real audio and Space pause',
                'click lyric to seek','save in place','advanced mode without data changes','style and font size',
                'simple and advanced lyric sweep buttons','Ctrl+Alt+K preserves template',
                'accessible toggles update actual sweep',
                'visible effects entry opens all 16 styles and all 7 animations without advanced editing',
                'selecting fade after static template initializes animation duration and undoes once',
                'template categories and complete visual presets','background import','cancel export','direct export and result playback','1000/1440 layouts',
                'draft included in Save and keyboard Save/SaveAs','invalid drafts preserved and export blocked',
                'advanced save and export include pending lyrics',
                'export request includes draft','last export playback after editing','next song preserves original and recovers pending lyrics'],
                'screenshots':screenshots,'qmlWarnings':warnings,'syntheticAudio':True,'cloudRequests':0}
            (target/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(report,ensure_ascii=False))
        except Exception:
            (target/'failure-state.json').write_text(json.dumps(studio.state,ensure_ascii=False,indent=2))
            if studio._job_dir:
                shutil.copytree(studio._job_dir,target/'failed-job',dirs_exist_ok=True)
            raise
        finally:
            studio.shutdown(); provider.shutdown(); window.close(); APPLICATION.processEvents()


if __name__=='__main__':
    main()
