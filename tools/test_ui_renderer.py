"""Isolated synthetic publisher/renderer regression; no DJ or paid services."""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.clock import monotonic_ns
from app.styles import DEFAULT_STYLE


def publish(runtime, long_text=False):
    runtime.mkdir(parents=True, exist_ok=True)
    timelines = json.loads((ROOT / 'fixtures/replay_timelines.json').read_text())
    style = dict(DEFAULT_STYLE)
    if long_text:
        text = '沿着节拍慢慢亮起' * 62
        timelines['demo:A'] = {'schemaVersion': 1, 'versionId': 'demo:A', 'durationMs': 120000,
                              'quality': 'word', 'provenance': 'synthetic UI stress case',
                              'segments': [{'startMs': 0, 'endMs': 60000, 'text': text,
                                            'words': [{'startMs': index * 60000 / len(text),
                                                       'endMs': (index + 1) * 60000 / len(text),
                                                       'text': char} for index, char in enumerate(text)]},
                                           {'startMs': 90000, 'endMs': 110000, 'text': '下一句保持清晰',
                                            'words': [{'startMs': 90000 + index * 2500,
                                                       'endMs': 90000 + (index + 1) * 2500,
                                                       'text': char} for index, char in enumerate('下一句保持清晰')]}]}
        style.update(fontSize=96, letterSpacing=8)
    epoch, sequence = str(uuid.uuid4()), 0
    while True:
        captured = monotonic_ns()
        sequence += 1
        raw = {key: {'versionId': 'demo:' + key, 'title': '合成独立客屏测试 ' + key, 'path': '',
                     'durationMs': 120000, 'positionMs': 6000 if long_text else 9500,
                     'speed': 1.0, 'capturedNs': captured, 'validUntilNs': captured + 350_000_000,
                     'playing': False, 'generation': 1} for key in ('A', 'B')}
        state = {'mode': 'replay', 'inputMode': 'replay', 'activeDeck': 'A', 'selectedSource': 'auto',
                 'effectiveSource': 'A', 'playing': False, 'speed': 1.0, 'loopEnabled': False,
                 'scene': 'amber', 'outputOpen': True, 'stale': False, 'bridgeRunning': True,
                 'bridgeEpoch': epoch, 'bridgeSequence': sequence, 'syntheticUIFrame': True,
                 'deckA': dict(raw['A']), 'deckB': dict(raw['B']), 'style': style}
        payload = {'state': state, 'raw': raw, 'timelines': timelines}
        temporary = runtime / 'program.tmp'
        temporary.write_text(json.dumps(payload, ensure_ascii=False))
        os.replace(temporary, runtime / 'program.json')
        time.sleep(0.04)


def main():
    parser = argparse.ArgumentParser(description='独立客屏合成 TTL、重连与长句验证')
    parser.add_argument('--publisher', action='store_true')
    parser.add_argument('--long', action='store_true')
    parser.add_argument('--runtime', default=str(ROOT / 'runtime/test-20261009/ui/renderer'))
    args = parser.parse_args()
    runtime = Path(args.runtime).resolve()
    if args.publisher:
        publish(runtime, args.long)
        return 0

    from scripts.repair_qt_metadata import repair_qt_metadata
    repair_qt_metadata()
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QT_QUICK_BACKEND', 'software')
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtGui import QFont
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from app.controller import FileController

    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    runtime.mkdir(parents=True, exist_ok=True)
    engines, controllers, windows, children = [], [], [], []
    warnings, checks, images, failures = [], [], [], []
    measurements = {}

    def start_publisher(path, long_text=False):
        arguments = [sys.executable, str(Path(__file__).resolve()), '--publisher', '--runtime', str(path)]
        if long_text:
            arguments.append('--long')
        process = subprocess.Popen(arguments, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        children.append(process)
        return process

    def stop_publisher(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)

    def wait_for(predicate, milliseconds=2000):
        deadline = monotonic_ns() + milliseconds * 1_000_000
        while monotonic_ns() < deadline:
            if predicate():
                return True
            QTest.qWait(10)
        return bool(predicate())

    def create_window(path):
        controller = FileController(ROOT, path)
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
        engine.rootContext().setContextProperty('controller', controller)
        engine.load(QUrl.fromLocalFile(str(ROOT / 'ui/Program.qml')))
        if not engine.rootObjects():
            raise AssertionError('Program.qml failed to load')
        window = engine.rootObjects()[0]
        controllers.append(controller)
        engines.append(engine)
        windows.append(window)
        return controller, window

    def items(window):
        def walk(item):
            yield item
            for child in item.childItems():
                yield from walk(child)
        return {item.objectName(): item for item in walk(window.contentItem()) if item.objectName()}

    def capture(window, name):
        path = runtime / (name + '.png')
        if not window.grabWindow().save(str(path)):
            raise AssertionError('Screenshot failed: ' + name)
        images.append(str(path))

    try:
        standard = runtime / 'standard'
        producer = start_publisher(standard)
        controller, window = create_window(standard)
        names = items(window)
        assert wait_for(lambda: names['programView'].property('showLyrics')), 'Fresh synthetic lyrics did not reach output'
        assert controller.state['lyric']['text'], 'Fresh projection is empty'
        capture(window, 'fresh-output')
        checks.append('fresh independent synthetic publisher reaches real FileController and QML output')

        stop_publisher(producer)
        ended = monotonic_ns()
        assert wait_for(lambda: controller.state['stale'] and not names['programView'].property('showLyrics'), 700), 'Output failed to hide after publisher ended'
        measurements['hideAfterPublisherStopMs'] = (monotonic_ns() - ended) / 1_000_000
        assert not names['lyricLayer'].isVisible(), 'Expired lyric layer remains visible'
        capture(window, 'expired-output')
        checks.append('publisher disappearance causes TTL expiry and hides the actual QML lyric layer')

        producer = start_publisher(standard)
        assert wait_for(lambda: not controller.state['stale'] and names['programView'].property('showLyrics')), 'Fresh publisher restart did not restore output'
        capture(window, 'reconnected-output')
        checks.append('new publisher session restores valid lyric output')
        stop_publisher(producer)

        long_runtime = runtime / 'long'
        long_producer = start_publisher(long_runtime, True)
        long_controller, long_window = create_window(long_runtime)
        long_window.resize(640, 420)
        long_names = items(long_window)
        assert wait_for(lambda: long_names['programView'].property('showLyrics')), 'Long lyric did not project'
        QTest.qWait(120)
        line = long_names['currentLyricText']
        assert len(line.property('text')) == 496, 'Current long lyric was truncated'
        assert line.property('lineCount') > 1, 'Long lyric did not wrap'
        assert line.property('sweepEnabled'), 'Wrapped lyric lost its shaped multiline sweep'
        bottom = line.mapToScene(QPointF(0, line.height())).y()
        next_line = long_names['nextLyricText']
        next_bottom = next_line.mapToScene(QPointF(0, next_line.height())).y()
        note_top = long_names['lyricModeNote'].mapToScene(QPointF(0, 0)).y()
        measurements.update(longChars=496, longLines=line.property('lineCount'),
                            longFontPixelSize=line.property('font').pixelSize(),
                            longCurrentTextBottom=bottom, longNextTextBottom=next_bottom,
                            modeNoteTop=note_top, outputHeight=long_window.height())
        capture(long_window, 'long-small-output')
        assert bottom <= long_window.height(), f'Current long lyric clipped at {bottom:.1f}px / {long_window.height()}px'
        assert next_line.isVisible() and next_bottom <= note_top, f'Next lyric clipped or overlaps mode note at {next_bottom:.1f}px / {note_top:.1f}px'
        checks.append('496-character word-quality lyric preserves current text and wrapped sweep at 640x420')
        stop_publisher(long_producer)

        window.requestActivate()
        QTest.qWait(60)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        QTest.qWait(60)
        assert not window.isVisible(), 'Escape did not close the guest output window'
        checks.append('Escape closes the guest output window')
        assert not warnings, '\n'.join(warnings)
    except Exception as exc:
        failures.append(str(exc) or type(exc).__name__)
    finally:
        for child in children:
            stop_publisher(child)
        for controller in controllers:
            controller.timer.stop()
        result = {'passed': not failures, 'syntheticOnly': True, 'realDJValidated': False,
                  'runtimeIsolated': str(runtime), 'checks': checks, 'measurements': measurements,
                  'warnings': warnings, 'failures': failures, 'screenshots': images}
        (runtime / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
