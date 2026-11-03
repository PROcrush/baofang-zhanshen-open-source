"""Real Qt/worker/cache/render chain; silence fixture and injected cloud only."""
import json
import os
import socket
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.clock import monotonic_ns
from app.controller import Controller, FileController
from tests.test_preparation import FakeProcess, FAKE_KEY
from tests.test_cloud_pipeline import FakeTransport
from worker.cloud_pipeline import run_job


def main():
    report_dir = ROOT / 'runtime/test-20261009/recognition-flow'
    report_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='run-', dir=report_dir))
    audio, reference, config = (run_dir / name for name in ('合成静音样例.wav', '合成参考歌词.txt', 'test-only-config.json'))
    with wave.open(str(audio), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b'\0\0' * 24000)
    reference.write_text('往事随风\n', encoding='utf-8')
    config.write_text(json.dumps({'apiKey': FAKE_KEY, 'budgetLimitPTC': 1}), encoding='utf-8')
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    warnings, checks, screenshots, attempts, failures = [], [], [], [], []
    controller, renderer, transport = None, None, None

    def no_network(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('This test must not open a network connection')

    def wait_for(predicate, duration=6000):
        deadline = monotonic_ns() + duration * 1_000_000
        while monotonic_ns() < deadline:
            if predicate():
                return True
            QTest.qWait(10)
        return bool(predicate())

    def named(window, name):
        def walk(item):
            yield item
            for child in item.childItems():
                yield from walk(child)
        return next(item for item in walk(window.contentItem()) if item.objectName() == name)

    def load_ui(model, filename):
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
        engine.rootContext().setContextProperty('controller', model)
        engine.load(QUrl.fromLocalFile(str(ROOT / 'ui' / filename)))
        assert engine.rootObjects(), filename + ' failed to load'
        return engine, engine.rootObjects()[0]

    def capture(window, filename):
        QTest.qWait(80)
        target = report_dir / filename
        assert window.grabWindow().save(str(target))
        screenshots.append(str(target))

    with patch('socket.create_connection', no_network), patch.object(socket.socket, 'connect', no_network), patch.object(socket.socket, 'connect_ex', no_network):
        try:
            controller = Controller(ROOT, run_dir / 'controller')
            engine, window = load_ui(controller, 'Main.qml')
            assert wait_for(lambda: not controller.state['stale'])
            controller.index_audio(str(audio))
            assert wait_for(lambda: str(audio) in controller.file_proofs)
            version = controller.file_proofs[str(audio)]['versionId']
            controller.loadTrack(version, 'A')
            assert wait_for(lambda: controller.state['deckA'].get('versionId') == version)
            controller.selectSource('A')
            controller.seek(1.2)
            assert wait_for(lambda: abs(controller.state['deckA']['positionMs'] - 1200) < 1)
            track = controller.recognition_track('A')
            assert not controller.state['preparation']['config']['newSubmissionEnabled']
            controller.preparation_action('configure_file', str(config))
            controller.setNewSubmissionEnabled(True)
            controller.preparation_action('correct', 'A', track, str(reference))
            controller.planRecognition('A')
            assert wait_for(lambda: controller.state['preparation']['decks']['A']['phase'] == 'planned'), controller.state['preparation']
            plan = controller.state['preparation']['decks']['A']['plan']
            assert plan['sourcePath'] == str(audio) and plan['canSubmit']
            assert plan['estimatedCostPTC'] == .011
            window.setProperty('currentPage', 4)
            capture(window, 'reviewed-fixture-plan.png')
            checks.append('real file SHA256, actual preflight QProcess, reviewed plan and reference preselection')

            processes = []
            def fake_process(parent):
                process = FakeProcess(parent)
                processes.append(process)
                return process
            controller.preparation.process_factory = fake_process
            controller.startRecognition('A', plan['planId'])
            assert len(processes) == 1 and processes[0].action == 'start'
            process = processes[0]
            response = {'code': 0, 'utterances': [{'text': '往事随疯', 'start_time': 1000, 'end_time': 2000,
                         'words': [{'text': '往事', 'start_time': 1000, 'end_time': 1400},
                                   {'text': '随疯', 'start_time': 1600, 'end_time': 2000}]}]}
            transport = FakeTransport(queries=[response])
            receipt = Path(process.arguments[process.arguments.index('--receipt') + 1])
            result = run_job(process.request, receipt, action='start', key=FAKE_KEY,
                             transport=transport, notify=process.queue)
            assert result['phase'] == 'ready', result
            process.queue(result)
            process.finish()
            assert wait_for(lambda: controller.state['lyric']['text'] == '往事随风')
            cached = controller.library.timeline(version)
            assert cached['segments'][0]['text'] == '往事随风'
            assert cached['segments'][0]['startMs'] == 1000 and cached['segments'][0]['endMs'] == 2000
            assert cached['timingProvenance'] == 'corrected_provider_token'
            assert not cached['acousticAccuracyVerified']
            raw = json.loads(Path(result['timelinePath']).read_text())
            assert raw['segments'][0]['text'] == '往事随疯'
            capture(window, 'corrected-and-cached.png')
            checks.append('real worker injected transport → real correction → exact-file SQLite cache; raw ASR preserved')
            for format in ('srt', 'lrc'):
                path = report_dir / ('合成链路验证.' + format)
                controller.preparation.export('A', track, str(path), format)
                assert '往事随风' in path.read_text(encoding='utf-8')
            checks.append('real UTF-8 SRT/LRC exports contain corrected text and original time bounds')

            renderer = FileController(ROOT, run_dir / 'controller')
            output_engine, output_window = load_ui(renderer, 'Program.qml')
            assert wait_for(lambda: renderer.state['lyric']['text'] == '往事随风')
            assert named(output_window, 'lyricLayer').isVisible()
            assert named(output_window, 'currentLyricText').property('text') == '往事随风'
            capture(output_window, 'corrected-fixture-output.png')
            program = (run_dir / 'controller/program.json').read_text()
            assert FAKE_KEY not in program and 'preparation' not in json.loads(program)['state']
            checks.append('real FileController and Qt客屏 show corrected lyrics; renderer payload has no preparation config or Key')
            assert not warnings, warnings
            assert not attempts
        except Exception as exc:
            failures.append(str(exc) or type(exc).__name__)
        finally:
            if renderer:
                renderer.timer.stop()
            if controller:
                controller.shutdown()
    result = {'passed': not failures, 'syntheticAudio': True, 'realAsrTested': False,
              'cloudRequests': 0, 'networkAttempts': len(attempts), 'realCredentialsRead': False,
              'fixtureTransportStages': [call[0] for call in transport.calls] if transport else [],
              'checks': checks, 'warnings': warnings, 'failures': failures,
              'screenshots': screenshots, 'isolatedRuntime': str(run_dir)}
    (report_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (report_dir / 'README.md').write_text('# 0.3 实际模块串联验证\n\n合成静音 WAV 和注入的云响应；真实 Controller、后台预检、cloud worker、参考纠错、SQLite、字幕导出与 Qt 客屏。未上传真实歌曲，未核验真实 ASR。\n\n结果见 result.json；图片和导出文件中的歌词是测试数据。\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
