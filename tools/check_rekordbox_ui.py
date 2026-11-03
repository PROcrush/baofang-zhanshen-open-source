"""Offscreen synthetic UI/renderer regression, separate from actual DJ following."""
import json
import os
from pathlib import Path
import tempfile
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.clock import monotonic_ns
from app.controller import Controller, FileController
from app.rekordbox_adapter import adapt_rekordbox_frame
from app.serato_adapter import proof_is_current
from app.storage import inspect_audio
from tools.ui_state_smoke import named_items, click


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / 'runtime/rekordbox-follow-20261021/ui'
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    with tempfile.TemporaryDirectory(dir=evidence) as folder:
        controller = Controller(root, Path(folder))
        controller.stopBridge()
        # Only this synthetic test substitutes the process boundary. Production
        # uses the native reader and cannot accept its experimental frames.
        controller.start_bridge = lambda _: controller._state.update(bridgeRunning=True)
        proofs, paths = {}, []
        for i, side in enumerate('AB'):
            path = Path(folder) / (side + '.wav')
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                stream.writeframes(bytes([i, 0]) * 80000)
            proof = inspect_audio(path)
            paths.append(str(path))
            proofs[str(path)] = proof
            controller.timelines[proof['versionId']] = {'schemaVersion': 1, 'versionId': proof['versionId'],
                'durationMs': 10000, 'quality': 'sentence',
                'segments': [{'startMs': 1000, 'endMs': 8000, 'text': '合成界面验证' + side}]}
        sample = {'type': 'rekordbox_frame', 'mode': 'rekordbox', 'protocolVersion': 1,
            'epoch': 'rekordbox:ui', 'sequence': 1, 'positionOnly': True, 'producerVerified': True,
            'decks': [{'deck': side, 'available': True, 'trackPath': path,
                       'positionSeconds': 2 if side == 'A' else 6, 'normalRate': 1,
                       'playRequested': True} for side, path in zip('AB', paths)], 'fader': None}
        def publish():
            if controller.state['mode'] != 'rekordbox':
                return
            # macOS may update Finder metadata (ctime) after fixture creation.
            # This synthetic sender bypasses production's reindex queue, so
            # renew a stale proof by hashing again, preserving the exact version.
            for path in paths:
                if not proof_is_current(proofs[path], path):
                    renewed = inspect_audio(path)
                    assert renewed['versionId'] == proofs[path]['versionId']
                    proofs[path] = renewed
            now = monotonic_ns()
            sample.update(capturedNs=now, validUntilNs=now + 350_000_000)
            controller._state['bridgeRunning'] = True
            controller.raw = adapt_rekordbox_frame(sample, proofs)
            controller.tick()
        publisher = QTimer()
        publisher.timeout.connect(publish)
        publisher.start(25)
        guest = FileController(root, folder)
        engines, warnings = [], []
        for name, model in (('Main.qml', controller), ('Program.qml', guest)):
            engine = QQmlApplicationEngine()
            engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
            engine.rootContext().setContextProperty('controller', model)
            engine.load(QUrl.fromLocalFile(str(root / 'ui' / name)))
            assert engine.rootObjects(), name
            engines.append(engine)
        window, output = [engine.rootObjects()[0] for engine in engines]
        try:
            QTest.qWait(100)
            items = named_items(window)
            window.requestActivate()
            QTest.qWait(60)
            click(window, items['liveInputModeCombo'])
            QTest.keyClick(window, Qt.Key.Key_Home)
            QTest.keyClick(window, Qt.Key.Key_Down)
            QTest.keyClick(window, Qt.Key.Key_Down)
            QTest.keyClick(window, Qt.Key.Key_Return)
            QTest.qWait(100)
            assert controller.state['mode'] == 'rekordbox', (controller.state['mode'], items['liveInputModeCombo'].property('currentIndex'))
            assert not items['liveOverlayButton'].isVisible()
            for side in ('A', 'B', 'A'):
                click(window, items['source-' + side])
                QTest.qWait(100)
                assert controller.state['effectiveSource'] == side, (controller.state['selectedSource'], controller.state['selectionReason'], controller.state['bridgeRunning'])
                assert guest.state['effectiveSource'] == side
                assert guest.state['lyric']['text'].endswith(side)
                assert guest.state['deck' + side]['positionMs'] == (2000 if side == 'A' else 6000)
            click(window, items['clearOutputButton'])
            QTest.qWait(80)
            assert not guest.state['lyric']['visible']
            click(window, items['clearOutputButton'])
            QTest.qWait(80)
            assert guest.state['effectiveSource'] == 'A'
            for size in ((1440, 900), (1000, 700)):
                window.resize(*size)
                QTest.qWait(80)
                for name in ('liveInputModeCombo', 'connectSeratoButton', 'source-A', 'source-B',
                             'clearOutputButton', 'liveOutputButton'):
                    node = items[name]
                    point = node.mapToScene(QPointF(0, 0))
                    assert node.isVisible() and point.x() >= 0 and point.y() >= 0
                    assert point.x() + node.width() <= size[0] and point.y() + node.height() <= size[1], name
                assert window.grabWindow().save(str(evidence / f'rekordbox-{size[0]}.png'))
            publisher.stop()
            QTest.qWait(420)
            assert not guest.state['lyric']['visible'] and guest.state['stale']
            assert not warnings, warnings
            (evidence / 'checks.json').write_text(json.dumps({'synthetic': True,
                'checks': ['mode_entry', 'manual_ab', 'independent_renderer', 'hide_restore',
                           'small_window', 'ttl_expiry'], 'qmlWarnings': warnings}, indent=2))
            print('rekordbox synthetic UI/renderer checks passed; QML warnings=0')
        finally:
            publisher.stop()
            controller.shutdown()
            for engine in engines:
                engine.rootObjects()[0].close()


if __name__ == '__main__':
    main()
