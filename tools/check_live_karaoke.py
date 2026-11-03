"""Check actual QML KTV preview, independent output and transparent local layer.

Uses an isolated replay and original synthetic lyrics, never Serato or paid APIs.
"""
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPointF, QUrl
from PySide6.QtGui import QAccessible, QAccessibleActionInterface, QFont, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.controller import Controller, FileController
from tools.ui_state_smoke import click, named_items, visual_items


def synthetic_timeline():
    return {'schemaVersion':1, 'versionId':'demo:A', 'durationMs':120000, 'quality':'word',
        'segments':[
            {'text':'星光照亮今晚', 'startMs':2000, 'endMs':6000, 'words':[
                {'text':'星光', 'startMs':2200, 'endMs':3200},
                {'text':'照亮今晚', 'startMs':4200, 'endMs':5800}]},
            {'text':'跟着节奏一起唱', 'startMs':7000, 'endMs':9000, 'words':[
                {'text':'跟着节奏一起唱', 'startMs':7000, 'endMs':8800}]},
            {'text':'每句都有真实时码', 'startMs':9500, 'endMs':11000, 'words':[
                {'text':'每句都有真实时码', 'startMs':9500, 'endMs':10900}]}]}


def digest(image):
    return hashlib.sha256(image.convertToFormat(QImage.Format.Format_RGBA8888).constBits()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ.get('BAOFANG_UI_CHECK_DIR',str(root / 'runtime/lyric-sweep-20261021'))).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC',13))
    QQuickStyle.setStyle('Basic')
    warnings, checks, windows, engines = [], [], [], []
    with tempfile.TemporaryDirectory(prefix='fixture-', dir=evidence) as folder:
        controller = Controller(root, Path(folder))
        output = FileController(root, Path(folder))
        try:
            for filename, model in (('Main.qml', controller), ('Program.qml', output), ('SeratoOverlay.qml', output)):
                engine = QQmlApplicationEngine()
                engine.warnings.connect(lambda rows:warnings.extend(row.toString() for row in rows))
                engine.rootContext().setContextProperty('controller',model)
                engine.load(QUrl.fromLocalFile(str(root/'ui'/filename)))
                assert engine.rootObjects(), filename
                engines.append(engine)
                windows.append(engine.rootObjects()[0])
            main_window, guest, overlay = windows
            overlay.setWidth(854); overlay.setHeight(480); overlay.show()
            QTest.qWait(300)
            controller.timelines['demo:A'] = synthetic_timeline()
            controller.selectSource('A')
            controller.setLiveLayout('karaoke')
            items = named_items(main_window)

            def seek(position):
                controller.seek(position/1000)
                QTest.qWait(110)
                assert controller.state['deckA']['positionMs'] == position
                assert output.state['lyric'].get('karaoke') == controller.state['lyric'].get('karaoke')
                return overlay.grabWindow()

            live_view=next(node for node in visual_items(items['programPreviewFrame']) if node.objectName()=='programView')
            views = [live_view, named_items(guest)['programView'], named_items(overlay)['programView']]
            zero = seek(0)
            assert digest(zero) == digest(seek(1000)), 'intro acquired a countdown/animation'
            before = seek(2700)
            assert digest(before) != digest(seek(3000)), 'word sweep did not move'
            assert digest(seek(3300)) == digest(seek(4000)), 'word gap continued sweeping'
            assert digest(before) == digest(seek(2700)), 'backward seek changed the same source frame'
            for view in views:
                assert view.property('traditionalMode') and view.property('showLyrics')
                assert view.property('renderWordSweep')
            for view in views:
                nodes = {node.objectName():node for node in visual_items(view) if node.objectName()}
                assert nodes['traditionalKaraokeLayer'].isVisible()
                assert not nodes['lyricLayer'].isVisible()
                assert 'countdownLayer' not in nodes
            checks.append('all three shared QML paths use traditional KTV, real word sweep, gap hold and source-time seeks; no countdown')
            for position, index in ((6000,1),(7200,1),(9000,2)):
                seek(position)
                assert output.state['lyric']['karaoke']['lineIndex']==index
            checks.append('fixed alternating slots advance in lyric gaps and keep the next phrase in place')
            image = seek(2700)
            assert image.pixelColor(0,0).alpha()==0 and image.pixelColor(image.width()-1,image.height()-1).alpha()==0
            assert image.save(str(evidence/'transparent-word-sweep.png'))
            for width, height in ((1440,900),(1000,700)):
                main_window.resize(width,height)
                QTest.qWait(80)
                click(main_window,items['liveTab-style'])
                QTest.qWait(60)
                for name in ('liveLyricSweep','liveQuickKaraoke','liveQuickStandard','liveQuickDynamic','clearOutputButton'):
                    node=items[name]
                    corner=node.mapToScene(QPointF(0,0))
                    assert node.isVisible() and corner.x()>=0 and corner.y()>=0
                    assert corner.x()+node.width()<=width and corner.y()+node.height()<=height, name
                assert main_window.grabWindow().save(str(evidence/f'live-{width}.png'))
            checks.append('three simple layout choices and hide control stay visible at 1440x900 and 1000x700')
            original_style=controller.state['style']
            items['outputStylePanel'].setProperty('libraryVisible',False)
            items['style-presetLibrary'].setProperty('selectedCategory','科技霓虹')
            items['style-presetSearch'].setProperty('text','HUD')
            assert items['liveStyleSettings'].property('text')=='字幕特效'
            click(main_window,items['liveStyleSettings'])
            assert main_window.property('currentPage')==3
            assert items['outputStylePanel'].property('libraryVisible')
            assert items['style-presetGrid'].property('count')==27
            assert controller.state['style']==original_style
            main_window.setProperty('currentPage',0);QTest.qWait(80)
            checks.append('visible effects entry reopens all 27 styles even after filters or fine-tuning, without changing appearance')
            main_window.setProperty('currentPage',3);QTest.qWait(80)
            from PySide6.QtCore import Qt
            for field, index, expected in (('liveEntrance',2,'ripple'),('liveExit',4,'glitch')):
                node=items['style-'+field]
                click(main_window,node)
                labels=['无','柔和叠化','涟漪波纹','色差分离','数字故障']
                delegate=next(n for n in visual_items(main_window.contentItem()) if n.isVisible() and n.property('text')==labels[index] and n.metaObject().className().startswith('ItemDelegate'))
                click(main_window,delegate)
                if controller.state['style'][field]!=expected:
                    main_window.grabWindow().save(str(evidence/'selector-failure.png'))
                assert controller.state['style'][field]==expected,(field,node.property('currentIndex'),controller.state.get('notice'))
                assert output.state['style'][field]==expected
            panel=items['outputStylePanel']
            panel.setProperty('samplePlaying',False);panel.setProperty('samplePosition',150)
            for width,height in ((1440,900),(1000,700)):
                main_window.resize(width,height);QTest.qWait(80)
                for name in ('style-liveEntrance','style-liveExit','style-previewEffects','style-libraryTab'):
                    node=items[name];corner=node.mapToScene(QPointF(0,0))
                    assert node.isVisible() and corner.x()>=0 and corner.y()>=0
                    assert corner.x()+node.width()<=width and corner.y()+node.height()<=height,name
                assert main_window.grabWindow().save(str(evidence/f'effects-{width}.png'))
            checks.append('entrance and exit selectors apply independently and persist to output; preview action fits both window sizes')
            from unittest.mock import patch
            with patch('app.controller.os.replace',side_effect=OSError('测试保存失败')):
                controller.setStyle('liveExit','dissolve')
            QTest.qWait(80)
            assert controller.state['style']['liveExit']=='glitch'
            assert items['style-effectError'].isVisible() and '未保存' in items['style-effectError'].property('text')
            controller.setStyle('liveExit','glitch');QTest.qWait(80)
            assert not items['style-effectError'].isVisible()
            checks.append('failed saving keeps previous effect and displays inline recovery message, cleared by a successful retry')
            panel.setProperty('sampleRequested',False)
            main_window.setProperty('currentPage',0);QTest.qWait(50)
            for effect in ('dissolve','ripple','rgb-split','glitch'):
                for field,position in (('liveEntrance',150),('liveExit',5850)):
                    controller.setStyle('liveEntrance','none');controller.setStyle('liveExit','none')
                    base=seek(position)
                    controller.setStyle(field,effect)
                    changed=seek(position)
                    assert digest(base)!=digest(changed),(field,effect)
                    assert digest(changed)==digest(seek(position)),('pause',field,effect)
                    seek(8000)
                    assert digest(changed)==digest(seek(position)),('seek',field,effect)
                    assert changed.pixelColor(0,0).alpha()==0
                    assert changed.save(str(evidence/f'{field}-{effect}.png'))
            checks.append('all four entrance and exit effects visibly render on transparent QML output and repeat exactly after pause/seek')
            controller.setStyle('liveEntrance','none');controller.setStyle('liveExit','none')
            click(main_window,items['liveQuickStandard'])
            assert output.state['style']['lineMode']=='double'
            assert not views[1].property('traditionalMode')
            click(main_window,items['liveQuickDynamic'])
            assert views[1].property('singleLine')
            click(main_window,items['liveQuickKaraoke'])
            assert output.state['style']['lineMode']=='karaoke' and items['liveQuickKaraoke'].property('selected')
            assert not items['liveQuickStandard'].property('selected')
            checks.append('actual layout clicks switch KTV/double/single without partial settings')
            for layout in ('double','single'):
                controller.setLiveLayout(layout)
                for effect in ('dissolve','ripple','rgb-split','glitch'):
                    controller.setStyle('liveEntrance','none')
                    baseline=seek(2150)
                    controller.setStyle('liveEntrance',effect)
                    assert digest(baseline)!=digest(seek(2150)),(layout,effect)
                    assert all(not view.property('traditionalMode') for view in views)
            controller.setStyle('liveEntrance','none');controller.setLiveLayout('karaoke')
            checks.append('all four transitions also render in simple double and single layouts through shared glyphs')
            sentence=copy.deepcopy(synthetic_timeline())
            sentence['quality']='sentence'
            for row in sentence['segments']:
                row.pop('words')
            controller.timelines['demo:A']=sentence
            a=seek(2700)
            assert digest(a)!=digest(seek(3000)), 'sentence wipe did not move'
            assert digest(a)==digest(seek(2700)), 'sentence wipe retained a later source frame'
            assert all(view.property('renderWordSweep') for view in views)
            assert controller.state['lyric']['quality']=='sentence'
            assert controller.state['lyric']['sweepTiming']=='sentence'
            assert image.save(str(evidence/'word-frame.png'))
            assert a.save(str(evidence/'sentence-frame.png'))
            checks.append('sentence-only lyrics estimate a source-time wipe in all three views, keeping original timing and sentence quality')
            for layout in ('karaoke','double','single'):
                controller.setLiveLayout(layout)
                seek(2700)
                click(main_window,items['liveLyricSweep'])
                assert controller.state['style']['sweepMode']=='sentence'
                assert all(not view.property('renderWordSweep') for view in views)
                click(main_window,items['liveLyricSweep'])
                assert controller.state['style']['sweepMode']=='word'
                assert all(view.property('renderWordSweep') for view in views)
                assert digest(seek(2700))!=digest(seek(3000)), layout
            checks.append('actual sweep button toggles all three layouts and output paths without changing layout')
            action=QAccessible.queryAccessibleInterface(items['liveLyricSweep']).actionInterface()
            for mode in ('sentence','word'):
                action.doAction(QAccessibleActionInterface.toggleAction()); QTest.qWait(80)
                assert controller.state['style']['sweepMode']==mode
                assert items['liveLyricSweep'].property('checked')==(mode=='word')
            checks.append('accessible toggle updates actual sweep and selected state together')
            from app.style_catalog import style_catalog
            for preset in style_catalog():
                controller.setStylePreset(preset['id'])
                before=seek(2700)
                assert all(view.property('renderWordSweep') for view in views), preset['id']
                assert digest(before)!=digest(seek(3000)), preset['id']
            checks.append('all 27 live presets keep sentence sweep enabled and visibly advance')
            from PySide6.QtCore import Qt
            main_window.requestActivate()
            QTest.qWait(50)
            modifiers=(Qt.KeyboardModifier.MetaModifier if __import__('sys').platform=='darwin' else Qt.KeyboardModifier.ControlModifier)|Qt.KeyboardModifier.AltModifier
            QTest.keyClick(main_window,Qt.Key.Key_K,modifiers)
            QTest.qWait(80)
            assert controller.state['style']['sweepMode']=='sentence'
            QTest.keyClick(main_window,Qt.Key.Key_K,modifiers)
            QTest.qWait(80)
            assert controller.state['style']['sweepMode']=='word'
            checks.append('Ctrl+Alt+K toggles live sweep using the actual QML shortcut')
            controller.setStyle('liveEntrance','ripple');controller.setStyle('liveExit','glitch')
            seek(5850)
            controller.clearOutput()
            QTest.qWait(100)
            assert all(not view.property('showLyrics') for view in views)
            hidden=overlay.grabWindow()
            assert all(hidden.pixelColor(x,y).alpha()==0 for x,y in ((30,30),(400,350),(700,410)))
            controller.restoreOutput()
            controller.timelines['demo:A']=synthetic_timeline()
            seek(2700)
            controller.stopBridge()
            QTest.qWait(100)
            assert all(not view.property('showLyrics') for view in views)
            checks.append('manual hiding and stopped source clear all lyric paths; no stale KTV preview survives')
            assert not warnings, warnings
            report={'passed':True,'checks':checks,'warnings':warnings,
                    'realSeratoValidated':False,'externalTVValidated':False,'cloudRequests':0}
            (evidence/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(report,ensure_ascii=False,indent=2))
        finally:
            controller.shutdown(); output.timer.stop()
            for window in windows:
                window.close()
            application.processEvents()


if __name__=='__main__':
    main()
