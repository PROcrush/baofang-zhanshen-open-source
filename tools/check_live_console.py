"""Read actual paused Serato into Qt + independent renderer; never control DJ."""
import json
import sys
from pathlib import Path

from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.clock import monotonic_ns
from app.controller import Controller
from app.messages import validate_program_payload

root = Path(__file__).resolve().parents[1]
runtime = root / 'runtime/serato-live-console'
app = QApplication(sys.argv)
app.setFont(QFont('PingFang SC', 13))
QQuickStyle.setStyle('Basic')
controller = Controller(root, runtime)
engine = QQmlApplicationEngine()
warnings = []
engine.warnings.connect(lambda values: warnings.extend(value.toString() for value in values))
engine.rootContext().setContextProperty('controller', controller)
engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
assert engine.rootObjects(), 'Console QML did not load'
window = engine.rootObjects()[0]
try:
    controller.setInputMode('serato')
    for _ in range(100):
        QTest.qWait(100)
        if len(controller.raw) == 2 and not controller.index_pending and all(deck['identityReady'] for deck in controller.raw.values()):
            break
    assert len(controller.raw) == 2 and not controller.state['stale'], controller.state['bridgeStatus']
    assert controller.state['videoFader'] >= 0
    assert all(not deck['versionId'].startswith('demo:') for deck in controller.raw.values())
    assert all(deck['identityReady'] for deck in controller.raw.values()), 'Actual files did not complete stable version binding'
    assert all(monotonic_ns() - deck['capturedNs'] < 350_000_000 for deck in controller.raw.values())
    observed = {key: {'title': deck['title'], 'positionMs': deck['positionMs'], 'speed': deck['speed'],
                      'identityReady': deck['identityReady'], 'cacheState': controller.state['deck' + key]['cacheState']}
                for key, deck in controller.raw.items()}
    window.grabWindow().save(str(runtime / 'console.png'))
    controller.setStyle('fontSize', 60)
    controller.setStyle('letterSpacing', 3)
    controller.setStyle('brightness', .75)
    controller.openOutput()
    QTest.qWait(1400)
    assert controller.output and controller.output.poll() is None
    payload = validate_program_payload(json.loads((runtime / 'program.json').read_text()))
    assert payload['state']['mode'] == 'serato' and payload['state']['style']['fontSize'] == 60
    # A reader stop must immediately remove lyrics and cannot restart playback.
    controller.stopBridge()
    controller.tick()
    assert not controller.raw and controller.state['stale'] and not controller.state['lyric']['visible']
    controller.connectSerato()
    QTest.qWait(1800)
    assert len(controller.raw) == 2 and not controller.state['stale'], controller.state['bridgeStatus']
    assert not warnings, '\n'.join(warnings)
    result = {'passed': True, 'scope': 'Actual readonly Serato -> Qt console -> independent renderer; paused observations only',
              'decks': observed, 'videoFader': controller.state['videoFader'], 'warnings': warnings,
              'stylePersistence': json.loads((runtime / 'settings.json').read_text()),
              'livePlaybackSeekLoopValidated': False, 'realLyricsDisplayed': False,
              'televisionValidated': False, 'recognitionSubmitted': False}
    (runtime / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
finally:
    controller.shutdown()
