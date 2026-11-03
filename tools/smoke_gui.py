"""Exercise real QML controls plus process freshness, using synthetic data only."""
import json
import sys
import shiboken6
from pathlib import Path

from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtGui import QFont
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.controller import Controller

root = Path(__file__).resolve().parent.parent
runtime = root / 'runtime/gui-smoke'
application = QApplication(sys.argv)
application.setFont(QFont('PingFang SC', 13))
QQuickStyle.setStyle('Basic')
controller = Controller(root, runtime)
engine = QQmlApplicationEngine()
warnings = []
engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
engine.rootContext().setContextProperty('controller', controller)
engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
assert engine.rootObjects(), 'QML root failed to load'
window = engine.rootObjects()[0]


def click(name):
    pending = [window]
    visited = set()
    item = None
    while pending:
        node = pending.pop()
        identity = shiboken6.getCppPointer(node)[0]
        if identity in visited:
            continue
        visited.add(identity)
        if node.objectName() == name:
            item = node
            break
        pending.extend(node.children())
        if hasattr(node, 'childItems'):
            pending.extend(node.childItems())
        if hasattr(node, 'contentItem') and node.contentItem():
            pending.append(node.contentItem())
    assert item is not None, f'Control missing: {name}'
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(point.x()), round(point.y())))
    QTest.qWait(180)


try:
    QTest.qWait(300)
    assert not controller.state['stale'], 'Replay bridge did not deliver valid data'
    click('playbackButton')
    assert controller.state['playing'], 'Start control did not reach bridge'
    controller.seek(9)
    QTest.qWait(180)
    assert controller.state['lyric']['visible'], 'Lyric did not bind to valid playhead'
    click('playbackButton')
    assert not controller.state['playing'], 'Pause control did not reach bridge'
    paused = controller.state['deckA']['positionMs']
    QTest.qWait(150)
    assert abs(paused - controller.state['deckA']['positionMs']) < 1, 'Paused clock drifted'
    click('clearOutputButton')
    assert not controller.state['lyric']['visible'], 'Manual hide did not take effect'
    click('source-A')
    assert controller.state['lyric']['visible'], 'Manual source did not restore lyric'
    controller.setSpeed(1.2)
    QTest.qWait(150)
    assert controller.state['speed'] == 1.2
    click('loopButton')
    assert controller.state['loopEnabled']
    controller.toggleLoop()
    QTest.qWait(100)
    window.grabWindow().save(str(runtime / 'console.png'))
    click('disconnectButton')
    QTest.qWait(400)
    assert controller.state['stale'] and not controller.state['lyric']['visible'], 'Stale data still projects lyrics'
    click('disconnectButton')
    assert not controller.state['stale'], 'Reconnect did not recover current snapshot'
    click('liveOutputButton')
    QTest.qWait(1800)
    assert controller.output is not None and controller.output.poll() is None, 'Independent renderer failed to launch'
    window.setProperty('currentPage', 1)
    QTest.qWait(150)
    window.grabWindow().save(str(runtime / 'library.png'))
    window.setProperty('currentPage', 2)
    QTest.qWait(150)
    window.grabWindow().save(str(runtime / 'diagnostics.png'))
    window.setProperty('currentPage', 0)
    window.resize(1000, 700)
    QTest.qWait(150)
    window.grabWindow().save(str(runtime / 'console-small.png'))
    assert not warnings, '\n'.join(warnings)
    result = {'passed': True, 'scope': 'QML controls, C++ clock, replay bridge, pause/seek/speed/loop/hide, stale/reconnect, separate renderer',
              'warnings': warnings, 'realDJValidated': False}
    (runtime / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
finally:
    controller.shutdown()
