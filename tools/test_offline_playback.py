"""Batch preparation, cache backup and disconnected restart with real Qt modules."""
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
from PySide6.QtWidgets import QApplication, QFileDialog
from app.clock import monotonic_ns
from app.controller import Controller, FileController
from tests.test_preparation import FakeProcess, FAKE_KEY
from tests.test_cloud_pipeline import FakeTransport
from worker.cloud_pipeline import run_job


def main():
    report_dir = ROOT / 'runtime/test-20261009/offline-playback'
    report_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='run-', dir=report_dir))
    folder = run_dir / '待演出曲库'
    folder.mkdir()
    audio = []
    for i, suffix in enumerate(('甲', '乙', '丙')):
        path = folder / ('合成静音歌曲' + suffix + '.wav')
        with wave.open(str(path), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            # Different fixture bytes create three actual content hashes.
            handle.writeframes(i.to_bytes(2, 'little', signed=True) * 48000)
        audio.append(path)
    config = run_dir / 'test-only-config.json'
    config.write_text(json.dumps({'apiKey': FAKE_KEY, 'budgetLimitPTC': 1}))
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    checks, warnings, failures, attempts, stages, screenshots = [], [], [], [], [], []
    controller, renderer, processes = None, None, []
    engines = []

    def no_network(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('Offline playback must not contact any network')

    def wait_for(predicate, duration=10000):
        deadline = monotonic_ns() + duration * 1_000_000
        while monotonic_ns() < deadline:
            if predicate():
                return True
            QTest.qWait(10)
        return bool(predicate())

    def load_ui(model, filename):
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
        engine.rootContext().setContextProperty('controller', model)
        engine.load(QUrl.fromLocalFile(str(ROOT / 'ui' / filename)))
        assert engine.rootObjects(), filename + ' failed to load'
        engines.append(engine)
        return engine.rootObjects()[0]

    def capture(window, filename):
        QTest.qWait(80)
        path = report_dir / filename
        assert window.grabWindow().save(str(path))
        screenshots.append(str(path))

    def payload(track, text):
        return {'schemaVersion': 1, 'versionId': track['versionId'], 'durationMs': 6000,
                'quality': 'sentence', 'acousticAccuracyVerified': False,
                'segments': [{'startMs': 1000, 'endMs': 3000, 'text': text}]}

    patches = [patch('socket.create_connection', no_network), patch.object(socket.socket, 'connect', no_network),
               patch.object(socket.socket, 'connect_ex', no_network)]
    for item in patches:
        item.start()
    try:
        runtime = run_dir / 'controller'
        controller = Controller(ROOT, runtime)
        window = load_ui(controller, 'Main.qml')
        assert wait_for(lambda: not controller.state['stale'])
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(folder)):
            controller.importAudioFolder()
        assert wait_for(lambda: controller.state['offlineLibrary']['total'] == 3 and
                        controller.state['offlineLibrary']['indexingCount'] == 0)
        tracks = [controller.library_track(controller.file_proofs[str(path)]['versionId']) for path in audio]
        cached = payload(tracks[0], '离线歌曲甲')
        controller.library.add_timeline(cached, tracks[0])
        controller.preparation_timeline(tracks[0]['versionId'], cached)
        assert controller.state['offlineLibrary']['ready'] == 1
        checks.append('user-selected folder scans in child; three real file identities indexed, one cache already ready')

        controller.preparation_action('configure_file', str(config))
        controller.setNewSubmissionEnabled(True)
        controller.planLibraryBatch()
        assert wait_for(lambda: controller.state['offlineLibrary']['batch']['status'] == 'planned'), controller.state['offlineLibrary']
        batch = controller.state['offlineLibrary']['batch']
        assert batch['total'] == 3 and batch['ready'] == 1 and batch['planned'] == 2
        assert batch['estimatedCostPTC'] == .022 and batch['canStart']
        window.setProperty('currentPage', 1)
        capture(window, 'batch-review-ready.png')
        checks.append('actual serial preflight skips cached track and reviews only two new tasks at total .022 fixture PTC')

        def factory(parent):
            process = FakeProcess(parent)
            processes.append(process)
            return process
        controller.preparation.process_factory = factory
        controller.startLibraryBatch(batch['id'])
        cursor = 0
        for _ in range(2):
            assert len(processes) == cursor + 1
            process = processes[cursor]
            assert process.action == 'start'
            title = controller.library.get_track(process.request['versionId'])['title']
            text = '离线歌曲' + title[-1]
            transport = FakeTransport(queries=[{'code': 0, 'utterances': [
                {'text': text, 'start_time': 1000, 'end_time': 3000}]}])
            receipt = Path(process.arguments[process.arguments.index('--receipt') + 1])
            result = run_job(process.request, receipt, action='start', key=FAKE_KEY,
                             transport=transport, notify=process.queue)
            assert result['phase'] == 'ready', result
            stages.extend(call[0] for call in transport.calls)
            process.queue(result)
            process.finish()
            cursor += 1
        assert wait_for(lambda: controller.state['offlineLibrary']['ready'] == 3)
        assert controller.state['offlineLibrary']['batch']['status'] == 'completed'
        assert len(list(controller.library.cache_directory.glob('*.json'))) == 3
        checks.append('single-flight fixture cloud jobs publish three persistent SHA lyric files and complete batch')

        backup = run_dir / 'lyrics-only-backup'
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(backup)):
            controller.exportLyricCache()
        assert (backup / 'manifest.json').is_file()
        assert not list(backup.glob('*.wav'))
        controller.setOfflineOnly(True)
        assert controller.state['offlineLibrary']['offlineOnly']
        controller.shutdown()
        controller = Controller(ROOT, runtime)
        window2 = load_ui(controller, 'Main.qml')
        window2.setProperty('currentPage', 1)
        assert controller.preparation.offline_only and not controller.preparation._key
        assert controller.state['offlineLibrary']['ready'] == 3
        assert not controller.preparation.processes
        assert controller.state['offlineLibrary']['batch']['status'] == 'paused'
        capture(window2, 'offline-restarted-library.png')
        checks.append('offline preference and all three lyric files survive real Controller shutdown/restart without Key or cloud children')

        assert wait_for(lambda: not controller.state['stale'])
        version = tracks[1]['versionId']
        controller.loadTrack(version, 'A')
        assert wait_for(lambda: controller.state['deckA'].get('versionId') == version)
        controller.selectSource('A')
        controller.seek(1.4)
        assert wait_for(lambda: controller.state['lyric']['text'] == '离线歌曲乙')
        renderer = FileController(ROOT, runtime)
        output = load_ui(renderer, 'Program.qml')
        assert wait_for(lambda: renderer.state['lyric']['text'] == '离线歌曲乙')
        capture(output, 'offline-cached-lyrics-output.png')
        controller.preparation.timer.timeout.emit()
        assert not controller.preparation.processes
        before = len(controller.preparation.processes)
        controller.queryLibraryTrack(version)
        assert len(controller.preparation.processes) == before
        controller.planLibraryTrack(version)
        assert controller.state['offlineLibrary']['batch']['ready'] == 1
        assert not controller.state['offlineLibrary']['batch']['canStart']
        text = (runtime / 'program.json').read_text()
        exported = json.loads(text)
        assert 'offlineLibrary' not in exported['state'] and 'preparation' not in exported['state']
        assert FAKE_KEY not in text
        checks.append('disconnected local lyric seek and actual FileController/Qt客屏 display; timer/query cannot spawn cloud workers')
        assert not warnings, warnings
        assert not attempts
    except Exception as exc:
        failures.append(str(exc) or type(exc).__name__)
    finally:
        if renderer:
            renderer.timer.stop()
        if controller and not controller.shutting_down:
            controller.shutdown()
        for item in reversed(patches):
            item.stop()
    result = {'passed': not failures, 'syntheticAudio': True, 'realAsrTested': False,
              'cloudRequests': 0, 'networkAttempts': len(attempts), 'realCredentialsRead': False,
              'fixtureTransportStages': stages, 'checks': checks, 'warnings': warnings,
              'failures': failures, 'screenshots': screenshots, 'isolatedRuntime': str(run_dir)}
    (report_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (report_dir / 'README.md').write_text('# 离线曲库完整验证\n\n三个本机测试WAV，其中一首已有缓存，另外两首使用注入云响应。实际后台预检、批量调度、歌词文件、备份、关闭和重启、无Key离线播放、FileController/Qt客屏均被验证。socket调用被禁止，未上传真实歌曲，图片中的歌词是测试数据。\n\n结果见result.json。\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
