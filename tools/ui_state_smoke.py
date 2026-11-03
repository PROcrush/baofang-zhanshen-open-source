"""Render synthetic UI frames; this never connects to Serato or audio devices."""
import argparse
import json
import os
from pathlib import Path

from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

from PySide6.QtCore import QObject, Property, QPointF, QTimer, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def synthetic_state(input_mode):
    def deck(key, cache):
        return {'title': '原创同步样例 ' + key + ' · 合成测试', 'path': '内置合成回放',
                'versionId': 'demo:' + key, 'positionMs': 15000, 'durationMs': 120000,
                'positionText': '00:15.000', 'playing': key == 'A', 'valid': True,
                'quality': 'word', 'cacheState': cache,
                'cacheLabel': '缓存就绪' if cache == 'ready' else '已挂起'}
    return {'mode': 'replay' if input_mode == 'replay' else 'serato-readonly',
            'inputMode': input_mode, 'modeLabel': '合成 UI 测试', 'syntheticUIFrame': True,
            'status': '合成 UI 状态 · 未连接真实 Serato',
            'bridgeStatus': '合成帧，仅验证界面与输出绑定',
            'notice': '本次未读取 Serato、不播放声音、不发送设备指令。',
            'videoFader': 0.51, 'selectionReason': '合成帧：51% 中心带保持 A，未驱动真实 DJ。',
            'playing': False, 'speed': 1.0, 'activeDeck': 'A', 'selectedSource': 'auto',
            'effectiveSource': 'A', 'deckA': deck('A', 'ready'), 'deckB': deck('B', 'suspended'),
            'lyric': {'visible': True, 'text': '灯光沿着节拍慢慢亮起', 'nextText': '每一次切换都有依据',
                      'quality': 'word', 'progress': 0.42, 'countdownMs': 0},
            'style': {'fontFamily': 'PingFang SC', 'fontSize': 48, 'letterSpacing': 0,
                      'brightness': 1, 'sweepMode': 'word', 'outputMode': 'standard',
                      'dynamicBackground': True, 'countdownEnabled': True},
            'library': [], 'diagnostics': {'summary': '合成测试报告', 'checks': [
                {'label': '数据来源', 'status': 'warning', 'detail': '仅为合成 UI 帧，不证明实际 Serato 接入。'}]},
            'outputOpen': True, 'stale': False, 'scene': 'amber', 'loopEnabled': False}


class SmokeController(QObject):
    stateChanged = Signal()

    def __init__(self, mode):
        super().__init__()
        from app.lyric_paint import register_lyric_types
        register_lyric_types()
        self.data = synthetic_state(mode)

    @Property('QVariantList', constant=True)
    def stylePresets(self):
        from app.style_catalog import style_catalog
        return style_catalog()

    @Slot(str)
    def setStylePreset(self, name):
        self.setStylePresetScheme(name, 0)

    @Slot(str, int)
    def setStylePresetScheme(self, name, index):
        from app.styles import style_preset
        self.data['style'] = style_preset(name, self.data['style'], index)
        self.emit_state()

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        return self.data

    def emit_state(self):
        self.stateChanged.emit()

    @Slot(str, 'QVariant')
    def setStyle(self, key, value):
        self.data['style'][key] = value
        self.emit_state()

    @Slot(bool)
    def setLyricSweep(self, enabled):
        self.setStyle('sweepMode','word' if enabled else 'sentence')

    @Slot(str)
    def setLiveLayout(self, layout):
        self.data['style'].update(lineMode=layout, outputMode='dynamic' if layout == 'single' else 'standard')
        self.emit_state()

    @Slot(str)
    def setInputMode(self, mode):
        self.data['inputMode'] = mode
        self.data['mode'] = 'replay' if mode == 'replay' else 'serato-readonly'
        self.emit_state()

    @Slot(str)
    def selectSource(self, source):
        self.data['selectedSource'] = source
        self.data['effectiveSource'] = 'A' if source == 'auto' else source
        self.emit_state()

    @Slot(str)
    def setScene(self, scene):
        self.data['scene'] = scene
        self.emit_state()

    @Slot()
    def clearOutput(self):
        self.selectSource('none')

    @Slot()
    def togglePlayback(self):
        if self.data['inputMode'] == 'replay':
            self.data['playing'] = not self.data['playing']
            self.emit_state()

    @Slot(float)
    def seek(self, seconds):
        if self.data['inputMode'] == 'replay':
            self.data['deckA']['positionMs'] = seconds * 1000
            self.emit_state()

    @Slot(float)
    def setSpeed(self, value):
        if self.data['inputMode'] == 'replay':
            self.data['speed'] = value
            self.emit_state()

    @Slot()
    def toggleLoop(self):
        if self.data['inputMode'] == 'replay':
            self.data['loopEnabled'] = not self.data['loopEnabled']
            self.emit_state()

    @Slot(str)
    def setDeck(self, key):
        if self.data['inputMode'] == 'replay':
            self.data['activeDeck'] = key
            self.emit_state()

    @Slot(str, str)
    def loadTrack(self, version, key):
        self.setDeck(key)

    @Slot()
    def simulateDisconnect(self):
        self.data['stale'] = True
        self.emit_state()

    @Slot()
    def reconnect(self):
        self.data['stale'] = False
        self.emit_state()

    @Slot()
    def openOutput(self):
        self.data['outputOpen'] = True
        self.emit_state()

    @Slot()
    def closeOutput(self):
        self.data['outputOpen'] = False
        self.emit_state()

    @Slot()
    def connectSerato(self):
        self.reconnect()
        self.refreshProbe()

    @Slot()
    def stopBridge(self):
        self.simulateDisconnect()

    @Slot()
    def refreshProbe(self):
        self.data['notice'] = '这是合成 UI 状态，未执行真实接入或诊断。'
        self.emit_state()

    @Slot()
    def runProbe(self):
        self.refreshProbe()

    @Slot()
    def importAudio(self):
        self.refreshProbe()

    @Slot()
    def importTimeline(self):
        self.refreshProbe()

    @Slot()
    def rescanLibrary(self):
        self.refreshProbe()

    @Slot()
    def exportDiagnostics(self):
        self.refreshProbe()


def visual_items(item):
    yield item
    for child in item.childItems():
        yield from visual_items(child)


def named_items(window):
    return {item.objectName(): item for item in visual_items(window.contentItem()) if item.objectName()}


def click(window, item):
    center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    QTest.qWait(100)


def main():
    parser = argparse.ArgumentParser(description='合成 UI 帧验证，不连接真实 Serato')
    parser.add_argument('--input-mode', choices=['replay', 'serato'], default='serato')
    parser.add_argument('--page', choices=['performance', 'library', 'diagnostics', 'style'], default='performance')
    parser.add_argument('--small', action='store_true')
    parser.add_argument('--output-dir', default='runtime/ui-state-smoke')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    target = Path(args.output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    controller = SmokeController(args.input_mode)
    engines = []
    windows = []
    warnings = []
    for filename in ['Main.qml', 'Program.qml']:
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
        engine.rootContext().setContextProperty('controller', controller)
        engine.load(QUrl.fromLocalFile(str(root / 'ui' / filename)))
        if not engine.rootObjects():
            return 2
        engines.append(engine)
        windows.append(engine.rootObjects()[0])
    main_window, program_window = windows
    if args.small:
        main_window.setWidth(1000)
        main_window.setHeight(700)
    outcome = {'syntheticOnly': True, 'realDJValidated': False, 'inputMode': args.input_mode,
               'checks': [], 'screenshots': [], 'warnings': warnings}
    failures = []
    completed = []

    def run():
        try:
            main_items, program_items = named_items(main_window), named_items(program_window)
            for name in ['playbackButton', 'playheadSlider', 'speedSlider', 'loopButton']:
                assert main_items[name].isEnabled() == (args.input_mode == 'replay'), name
            assert abs(main_items['videoFaderMeter'].property('value') - 0.51) < 0.001
            outcome['checks'].append('read-only controls and normalized fader')
            if args.input_mode == 'serato':
                click(main_window, main_items['bridgeStopButton'])
                assert controller.data['stale'] and not program_items['programView'].property('showLyrics')
                click(main_window, main_items['connectSeratoButton'])
                assert not controller.data['stale'] and program_items['programView'].property('showLyrics')
                outcome['checks'].append('synthetic stop and reconnect buttons hide and restore guest lyrics')
            click(main_window, main_items['clearOutputButton'])
            assert controller.data['effectiveSource'] == 'none' and not program_items['programView'].property('showLyrics')
            click(main_window, main_items['source-auto'])
            assert program_items['programView'].property('showLyrics')
            outcome['checks'].append('clear output and automatic source buttons update guest lyrics')
            main_window.setProperty('currentPage', 3)
            QTest.qWait(120)
            click(main_window, main_items['style-adjustTab'])
            controller.setStyle('fontSize', 64)
            controller.setStyle('letterSpacing', 3)
            controller.setStyle('brightness', 0.65)
            QTest.qWait(100)
            font = program_items['currentLyricText'].property('font')
            assert font.pixelSize() == 64 and abs(font.letterSpacing() - 3) < 0.01
            assert abs(program_items['lyricLayer'].opacity() - 0.65) < 0.001
            click(main_window, main_items['style-sentence'])
            assert controller.data['style']['sweepMode'] == 'sentence'
            assert not program_items['programView'].property('renderWordSweep')
            click(main_window, main_items['style-dynamic'])
            assert controller.data['style']['outputMode'] == 'dynamic'
            assert not program_items['nextLyricText'].isVisible()
            outcome['checks'].append('style controls update independent guest output')
            controller.selectSource('B')
            QTest.qWait(100)
            assert not program_items['programView'].property('showLyrics')
            main_window.setProperty('currentPage', 0)
            controller.data['activeDeck'] = 'B'
            controller.data['deckB']['cacheState'] = 'missing'
            controller.emit_state()
            QTest.qWait(100)
            assert main_items['liveNextAction'].property('text') == '准备这首歌'
            click(main_window, main_items['liveNextAction'])
            assert main_window.property('currentPage') == 4, 'Missing-lyric action did not open preparation'
            main_window.setProperty('currentPage', 0)
            controller.data['deckB']['cacheState'] = 'suspended'
            controller.emit_state()
            QTest.qWait(100)
            assert main_items['liveNextAction'].property('text') == '查看原因'
            click(main_window, main_items['liveNextAction'])
            assert main_window.property('currentPage') == 2, 'Unavailable-source action did not open diagnostics'
            outcome['checks'].append('missing lyrics and unavailable playback guide to distinct actionable pages')
            if args.input_mode == 'serato':
                main_window.setProperty('currentPage', 0)
                controller.data['activeDeck'] = 'B'
                controller.data['speed'] = 0
                controller.data['deckB']['durationMs'] = 0
                controller.emit_state()
                QTest.qWait(100)
                assert 'B 盘' in main_items['sourcePositionTitle'].property('text'), 'requested deck label'
                assert main_items['speedValueText'].property('text') == '速度 0.00×', 'zero paused rate'
                assert not main_items['playheadSlider'].isVisible()
                assert not main_items['speedSlider'].isVisible()
                assert main_items['unknownDurationNotice'].isVisible(), 'unknown duration label'
                outcome['checks'].append('paused real rate and unknown duration remain honest')
            controller.selectSource('auto')
            controller.data['deckA']['cacheState'] = 'preparing'
            controller.emit_state()
            QTest.qWait(100)
            assert not program_items['programView'].property('showLyrics')
            outcome['checks'].append('suspended and unprepared selected sources hide lyrics')
            controller.data['deckA']['cacheState'] = 'ready'
            controller.data['lyric'].update(visible=False, countdownMs=4000)
            controller.emit_state()
            QTest.qWait(100)
            assert not program_items['programView'].property('showLyrics')
            assert 'countdownLayer' not in program_items, 'legacy countdown setting revived numeric countdown'
            assert 'style-countdownToggle' not in main_items
            controller.setStyle('dynamicBackground', False)
            QTest.qWait(100)
            assert not program_items['programView'].property('backgroundVisible')
            outcome['checks'].append('lyric gap has no countdown even with legacy setting enabled; black-background switch')
            controller.data = synthetic_state(args.input_mode)
            controller.emit_state()
            main_window.setProperty('currentPage', ['performance', 'library', 'diagnostics', 'style'].index(args.page))
            QTest.qWait(200)
            for window, name in [(main_window, 'console'), (program_window, 'program')]:
                path = target / (name + '.png')
                assert window.grabWindow().save(str(path))
                outcome['screenshots'].append(str(path))
            assert not warnings, '\n'.join(warnings)
            outcome['passed'] = True
            outcome['failures'] = []
            (target / 'result.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            completed.append(True)
        except Exception as exc:
            failures.append(str(exc) or type(exc).__name__)
            outcome['passed'] = False
            outcome['failures'] = failures
            (target / 'result.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
            print('UI smoke failed:', failures[-1])
        finally:
            application.quit()

    QTimer.singleShot(400, run)
    QTimer.singleShot(8000, application.quit)
    application.exec()
    return 1 if failures or not completed else 0


if __name__ == '__main__':
    raise SystemExit(main())
