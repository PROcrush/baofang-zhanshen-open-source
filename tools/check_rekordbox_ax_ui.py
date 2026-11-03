"""Synthetic AX-mode QML interaction checks; never reads a DJ app or permissions."""
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPointF, Qt, QUrl, Slot
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from tools.ui_state_smoke import SmokeController, click, named_items


class AxUiController(SmokeController):
    def __init__(self):
        super().__init__('serato')
        self.calls = []
        self.data['selectedSource'] = 'A'
        self.data['rekordboxAX'] = {
            'observed': {side: {'title': '同名歌曲 · 原版界面', 'positionSeconds': 2.1,
                                'available': True} for side in 'AB'},
            'bindings': {side: {'status': '待确认当前歌曲'} for side in 'AB'},
            'candidates': [{'title': '同名歌曲', 'versionId': 'sha256:' + str(i),
                            'path': '/合成路径/实际 Remix 与剪辑版本/' + name}
                           for i, name in enumerate(('原混音.wav', '另一剪辑.wav'))]}

    @Slot(str)
    def setInputMode(self, mode):
        self.data.update(inputMode=mode, mode=mode, selectedSource='A')
        self.emit_state()

    @Slot()
    def connectRekordboxAX(self):
        self.calls.append(('connect',))

    @Slot()
    def requestRekordboxAXAccess(self):
        self.calls.append(('access',))

    @Slot(str, str)
    def bindRekordboxAX(self, deck, version):
        self.calls.append(('bind', deck, version))
        candidate = next(row for row in self.data['rekordboxAX']['candidates']
                         if row['versionId'] == version)
        self.data['rekordboxAX']['bindings'][deck] = dict(candidate, status='已确认当前文件')
        self.emit_state()

    @Slot(str)
    def clearRekordboxAXBinding(self, deck):
        self.calls.append(('clear', deck))
        self.data['rekordboxAX']['bindings'][deck] = {'status': '待确认当前歌曲'}
        self.emit_state()


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root / 'runtime/rekordbox-ax-20261021/ui'
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    controller, warnings = AxUiController(), []
    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    engine.rootContext().setContextProperty('controller', controller)
    engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    try:
        QTest.qWait(100)
        window.requestActivate()
        QTest.qWait(80)
        items = named_items(window)
        assert items['deckPlaybackState-A'].property('text') == '播放中'
        assert items['deckPlaybackState-B'].property('text') == '已暂停'
        assert items['speedValueText'].isVisible()
        click(window, items['liveInputModeCombo'])
        QTest.keyClick(window, Qt.Key.Key_End)
        QTest.keyClick(window, Qt.Key.Key_Return)
        QTest.qWait(80)
        assert controller.data['mode'] == 'rekordbox_ax'
        assert not items['liveOverlayButton'].isVisible()
        assert not items['videoFaderMeter'].isVisible()
        assert not items['source-auto'].isEnabled()
        assert not items['speedValueText'].isVisible()
        assert items['rekordboxAXPlaybackNotice'].isVisible()
        for side in 'AB':
            assert items['deckPlaybackState-' + side].property('text') == '位置直读'
        for side in ('A', 'B', 'A'):
            click(window, items['source-' + side])
            assert controller.data['selectedSource'] == side
        click(window, items['connectSeratoButton'])
        click(window, items['rekordboxAXAccessButton'])
        assert controller.calls == [('connect',), ('access',)]
        for size in ((1440, 900), (1000, 700)):
            window.resize(*size)
            QTest.qWait(80)
            for name in ('liveInputModeCombo', 'connectSeratoButton', 'source-A', 'source-B',
                         'clearOutputButton', 'liveOutputButton', 'bindRekordboxAX-A',
                         'bindRekordboxAX-B', 'rekordboxAXAccessButton'):
                node = items[name]
                point = node.mapToScene(QPointF(0, 0))
                assert node.isVisible() and point.x() >= 0 and point.y() >= 0, name
                assert point.x() + node.width() <= size[0] and point.y() + node.height() <= size[1], name
            click(window, items['bindRekordboxAX-A'])
            popup = named_items(window)
            assert not popup['rekordboxAXBindingConfirm'].isEnabled()
            assert popup['rekordboxAXCandidateCombo'].property('currentIndex') == -1
            assert '同名歌曲' in popup['rekordboxAXObservedTitle'].property('text')
            assert window.grabWindow().save(str(evidence / f'ax-unbound-{size[0]}.png'))
            click(window, popup['rekordboxAXCandidateCombo'])
            QTest.keyClick(window, Qt.Key.Key_End)
            QTest.keyClick(window, Qt.Key.Key_Return)
            QTest.qWait(80)
            assert popup['rekordboxAXCandidatePath'].property('text').endswith('/另一剪辑.wav')
            assert popup['rekordboxAXBindingConfirm'].isEnabled()
            # Changing selection only previews the full file path; it cannot bind.
            assert not any(row[0] == 'bind' for row in controller.calls)
            assert window.grabWindow().save(str(evidence / f'ax-confirm-{size[0]}.png'))
            click(window, popup['rekordboxAXBindingConfirm'])
            assert controller.calls[-1] == ('bind', 'A', 'sha256:1')
            click(window, items['bindRekordboxAX-A'])
            popup = named_items(window)
            click(window, popup['rekordboxAXBindingClear'])
            assert controller.calls[-1] == ('clear', 'A')
            assert not controller.data['rekordboxAX']['bindings']['A'].get('versionId')
            click(window, popup['rekordboxAXBindingClose'])
            controller.calls = [('connect',), ('access',)]
        controller.data['rekordboxAX']['observed']['B']['available'] = False
        controller.emit_state()
        click(window, items['bindRekordboxAX-B'])
        popup = named_items(window)
        click(window, popup['rekordboxAXCandidateCombo'])
        QTest.keyClick(window, Qt.Key.Key_Home)
        QTest.keyClick(window, Qt.Key.Key_Return)
        assert not popup['rekordboxAXBindingConfirm'].isEnabled()
        assert not warnings, warnings
        (evidence / 'checks.json').write_text(json.dumps({'syntheticOnly': True,
            'realDJValidated': False, 'qmlWarnings': warnings,
            'checks': ['fourth_mode', 'manual_ab', 'no_auto_or_fader', 'no_inferred_playback_or_tempo', 'access_callback',
                       'explicit_file_confirmation', 'full_path', 'clear_binding',
                       'unavailable_deck_disallows_binding', '1440_and_1000_bounds']}, indent=2))
        print('rekordbox AX synthetic QML checks passed; QML warnings=0')
    finally:
        window.close()


if __name__ == '__main__':
    main()
