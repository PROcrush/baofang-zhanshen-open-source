"""Real Qt actions, replay clock, persisted sync and independent output placement."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFont, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.controller import Controller, FileController
from app.live_settings import place_output
from tools.ui_state_smoke import named_items, click, visual_items


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root / 'runtime/live-reliability-20261021'
    evidence.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory(prefix='baofang-live-', dir=evidence) as folder:
        application = QApplication([])
        application.setFont(QFont('PingFang SC',13))
        QQuickStyle.setStyle('Basic')
        hide_modifier=Qt.KeyboardModifier.MetaModifier if sys.platform=='darwin' else Qt.KeyboardModifier.ControlModifier
        controller = Controller(root, Path(folder))
        output = FileController(root, Path(folder))
        warnings = []
        engines = []
        for name, model in (('Main.qml',controller), ('Program.qml',output)):
            engine = QQmlApplicationEngine()
            engine.warnings.connect(lambda rows:warnings.extend(row.toString() for row in rows))
            engine.rootContext().setContextProperty('controller',model)
            engine.load(QUrl.fromLocalFile(str(root/'ui'/name)))
            assert engine.rootObjects(), name+' failed to load'
            engines.append(engine)
        window, guest = (engine.rootObjects()[0] for engine in engines)
        output.attach_output_window(guest)
        try:
            QTest.qWait(350)
            assert not controller.state['stale']
            items = named_items(window)

            def press(name):
                node = items.get(name)
                if node is None:
                    node = next((item for item in visual_items(window.contentItem()) if item.objectName()==name),None)
                assert node is not None and node.isVisible() and node.isEnabled(), name+' is not reachable'
                click(window,node)

            controller.seek(3)
            QTest.qWait(120)
            position = controller.state['deckA']['positionMs']
            progress = controller.state['lyric']['progress']
            press('liveOffsetLater')
            press('liveOffsetLater')
            assert controller.state['lyricOffsetMs']==200
            assert controller.state['deckA']['positionMs']==position
            assert controller.state['lyric']['progress']<progress
            assert output.state['lyricOffsets']=={'demo:A':200}
            press('liveOffsetReset')
            assert controller.state['lyricOffsetMs']==0
            press('source-B')
            press('liveOffsetEarlier')
            assert controller.state['lyricOffsets']=={'demo:B':-100}
            assert 'A 盘' in items['sourcePositionTitle'].property('text'), 'Lyric routing changed replay transport target'
            press('clearOutputButton')
            assert not output.state['lyric']['visible'] and controller.state['resumeSource']=='B'
            press('clearOutputButton')
            assert controller.state['selectedSource']=='B'
            window.requestActivate()
            QTest.qWait(80)
            QTest.keyClick(window,Qt.Key.Key_H,hide_modifier)
            QTest.qWait(80)
            assert controller.state['selectedSource']=='none'
            QTest.keyClick(window,Qt.Key.Key_H,hide_modifier)
            QTest.qWait(80)
            assert controller.state['selectedSource']=='B'
            for size in ((1440,900),(1000,700)):
                window.resize(*size)
                window.requestActivate()
                QTest.qWait(80)
                for page in range(8):
                    window.setProperty('currentPage',page)
                    QTest.qWait(60)
                    toggle=items['globalLyricsToggle']
                    assert toggle.isVisible()==(page!=0)
                    QTest.keyClick(window,Qt.Key.Key_H,hide_modifier)
                    QTest.qWait(60)
                    assert controller.state['selectedSource']=='none', f'page {page}: shortcut did not hide'
                    assert not output.state['lyric']['visible']
                    assert window.property('currentPage')==page
                    QTest.keyClick(window,Qt.Key.Key_H,hide_modifier)
                    QTest.qWait(60)
                    assert controller.state['selectedSource']=='B', f'page {page}: shortcut lost source'
                    if page!=0:
                        point=toggle.mapToScene(QPointF(0,0))
                        assert point.x()>=0 and point.x()+toggle.width()<=size[0]
                        press('globalLyricsToggle')
                        assert controller.state['selectedSource']=='none'
                        press('globalLyricsToggle')
                        assert controller.state['selectedSource']=='B'
                assert window.grabWindow().save(str(evidence/f'global-toggle-{size[0]}.png'))
            window.setProperty('currentPage',0)
            pending=controller.timelines['demo:B']
            controller.timelines['demo:B']=dict(pending,correction={'status':'needs_review','review':['待核对文字']})
            controller.tick()
            QTest.qWait(100)
            assert '待复核' in items['liveReadinessTitle'].property('text')
            assert items['deckCache-B'].property('tone')=='amber'
            assert items['liveNextAction'].property('text')=='复核歌词'
            assert output.state['deckB']['needsReview']
            assert window.grabWindow().save(str(evidence/'needs-review.png'))
            press('liveNextAction')
            assert window.property('currentPage')==4
            controller.timelines['demo:B']=pending
            controller.tick()
            window.setProperty('currentPage',0)
            press('source-auto')
            controller.setStylePreset('minimal')
            QTest.qWait(80)
            assert items['liveQuickDynamic'].property('selected')
            assert not items['liveQuickStandard'].property('selected')
            assert not named_items(guest)['nextLyricText'].isVisible()
            press('liveTab-style')
            press('liveQuickStandard')
            assert output.state['style']['lineMode']=='double'
            assert output.state['style']['outputMode']=='standard'
            assert named_items(guest)['nextLyricText'].isVisible()
            assert items['liveQuickStandard'].property('selected')
            from unittest.mock import patch
            with patch.object(controller,'save_settings',side_effect=OSError('simulated write failure')):
                press('liveQuickDynamic')
            assert items['liveQuickStandard'].property('selected')
            assert '未保存' in items['liveReadinessTitle'].property('text')
            press('liveQuickDynamic')
            assert output.state['style']['outputMode']=='dynamic'
            assert output.state['style']['lineMode']=='single'
            assert '未保存' not in items['liveReadinessTitle'].property('text')
            press('liveQuickStandard')
            controller.setStyle('dynamicBackground',True)
            press('liveTab-sync')
            for size in ((1440,900),(1000,700)):
                window.resize(*size)
                QTest.qWait(120)
                assert window.grabWindow().save(str(evidence/f'live-{size[0]}.png'))
                for name in ('clearOutputButton','liveOffsetEarlier','liveOffsetLater','liveOutputButton'):
                    node=items[name]
                    p=node.mapToScene(QPointF(0,0))
                    assert p.x()>=0 and p.y()>=0 and p.x()+node.width()<=size[0] and p.y()+node.height()<=size[1], name+' clipped'
            press('liveScreenSettings')
            press('liveFullscreenToggle')
            assert controller.state['outputTarget']['fullScreen']
            assert guest.visibility()==QWindow.Visibility.FullScreen
            assert window.grabWindow().save(str(evidence/'screen-settings.png'))
            press('liveFullscreenToggle')
            assert guest.visibility()!=QWindow.Visibility.FullScreen
            screen=application.primaryScreen()
            place_output(guest,{'screenName':screen.name(),'fullScreen':True})
            assert guest.screen()==screen and guest.visibility()==QWindow.Visibility.FullScreen
            place_output(guest,{'screenName':'missing-monitor-fixture','fullScreen':True})
            assert guest.visibility()!=QWindow.Visibility.FullScreen, 'Missing monitor did not leave fullscreen'
            QTest.keyClick(window,Qt.Key.Key_Escape)
            QTest.qWait(80)
            press('disconnectButton')
            QTest.qWait(400)
            assert not output.state['lyric']['visible'] and controller.state['stale']
            assert window.grabWindow().save(str(evidence/'disconnected.png'))
            press('liveNextAction')
            assert not controller.state['stale']
            press('liveOutputButton')
            QTest.qWait(750)
            assert controller.output and controller.output.poll() is None
            press('liveOutputButton')
            assert controller.output is None
            # Restart a real local QProcess without inspecting Serato or sending DJ commands.
            fixture_code='import json,time\nprint(json.dumps({"type":"serato_status","detail":"合成重连测试，不读取Serato"}),flush=True)\ntime.sleep(60)\n'
            def start_reader_fixture(mode):
                assert mode=='serato'
                controller._state['bridgeRunning']=True
                controller.bridge.setProgram(sys.executable)
                controller.bridge.setArguments(['-u','-c',fixture_code])
                controller.bridge.start()
            with patch.object(controller,'start_bridge',side_effect=start_reader_fixture):
                controller.connectSerato()
                assert controller.bridge.waitForStarted(1000)
                QTest.qWait(80)
                previous_pid=controller.bridge.processId()
                assert previous_pid>0
                controller._state.update(bridgeEpoch='fixture:old',bridgeSequence=20)
                controller.connectSerato()
                assert controller.bridge.waitForStarted(1000)
                assert controller.bridge.processId()>0 and controller.bridge.processId()!=previous_pid
                assert controller.state['bridgeEpoch'] is None and controller.state['bridgeSequence']==0
                assert controller.state['stale'] and not controller.state['lyric']['visible']
                QTest.qWait(80)
                assert '合成重连测试' in controller.state['bridgeStatus']
            controller.setInputMode('replay')
            QTest.qWait(150)
            preferences=json.loads(controller.settings_path.read_text())
            controller.shutdown()
            reloaded=Controller(root,Path(folder))
            try:
                assert reloaded.state['lyricOffsets']=={'demo:B':-100}
                assert reloaded.state['outputTarget']==preferences['outputTarget']
            finally:
                reloaded.shutdown()
            assert not warnings, '\n'.join(warnings)
            report={'passed':True,'warnings':warnings,'scope':['all eight pages support Ctrl+H and preserve manual source; global mouse button on other pages at both window sizes','pending review reaches deck badge, live warning, preparation action and independent renderer','single preset switches to double; failed layout save keeps prior state and retry clears error','real local QProcess reader fixture restarts with a new PID and drops old epoch; no Serato access','per-version sync persists without moving the source clock','real Qt fullscreen on current screen and missing-screen fallback','separate renderer starts and stops','1000x700 and 1440x900 visible controls'],
                    'realDJValidated':False,'externalTVValidated':False,'cloudRequests':0}
            (evidence/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(report,ensure_ascii=False,indent=2))
        finally:
            controller.shutdown()
            output.timer.stop()
            window.close()
            guest.close()


if __name__=='__main__':
    main()
