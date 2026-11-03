"""Kill/relaunch the actual app entry point in an isolated runtime.

Only the replay reader runs. Live readers are recorded test substitutes; no
DJ app, network, credentials or daily runtime is accessed.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def child(phase, runtime, evidence):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QT_QUICK_BACKEND', 'software')
    from scripts.repair_qt_metadata import repair_qt_metadata
    repair_qt_metadata()
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from unittest.mock import patch
    from app.controller import Controller
    from app.main import main as app_main
    from tools.check_preshow import find, wait_for
    from tools.ui_state_smoke import click

    started, warnings, controllers, failures = [], [], [], []

    class CheckedController(Controller):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            controllers.append(self)

        def start_bridge(self, mode):
            started.append(mode)
            if mode == 'replay':
                super().start_bridge(mode)

    original_exec = QApplication.exec

    def exercise():
        try:
            c = controllers[0]
            window = next(w for w in QGuiApplication.topLevelWindows() if w.title().startswith('包房战神 ·'))
            if phase == 'seed':
                assert wait_for(lambda: not c.state['stale'])
                c.selectSource('B')
                c.setLyricOffset(250)
                c.setStyle('liveEntrance', 'ripple')
                c.setOutputTarget(QGuiApplication.primaryScreen().name(), False)
                c.clearOutput()
                old_program = (runtime / 'program.json').read_bytes()
                (evidence / 'old-program.json').write_bytes(old_program)
                c.setInputMode('serato')
                (runtime / 'ready').write_text('ready')
                return  # Parent kills this actual main process, bypassing shutdown.

            expected = 'rekordbox_ax' if phase == 'ax' else 'serato'
            assert started == [expected], started
            assert c.state['mode'] == expected
            assert c.state['selectedSource'] == 'none'
            assert c.state['resumeSource'] == 'B'
            assert c.state['lyricOffsets'] == {'demo:B': 250}
            assert c.state['style']['liveEntrance'] == 'ripple'
            assert not c.state['outputRequested'] and c.output is None
            assert not c.raw and not c.file_proofs and not c.ax_bindings
            assert not c.state['lyric']['visible'] and c.state['stale']
            for width, height in ((1000, 700), (1440, 900)):
                window.resize(width, height)
                QTest.qWait(150)
                toggle = find(window, 'clearOutputButton')
                assert toggle is not None and toggle.isVisible()
                assert '恢复' in toggle.property('text'), toggle.property('text')
                window.grabWindow().save(str(evidence / f'{phase}-{width}x{height}.png'))
            click(window, find(window, 'clearOutputButton'))
            QTest.qWait(80)
            assert c.state['selectedSource'] == 'B'
            assert not c.state['lyric']['visible'], 'restoring source bypassed freshness'
            c.clearOutput()
            if phase == 'fresh':
                c.setInputMode('replay')
                assert wait_for(lambda: not c.state['stale'])
                assert not c.state['lyric']['visible'], 'new data bypassed manual hide'
                c.restoreOutput()
                assert wait_for(lambda: c.state['effectiveSource'] == 'B')
                assert c.state['deckB']['versionId'] == 'demo:B'
                c.clearOutput()
            (evidence / f'{phase}-result.json').write_text(json.dumps(
                {'passed': True, 'started': started, 'mode': c.state['mode'], 'warnings': warnings}, ensure_ascii=False))
        except Exception as exc:
            import traceback
            traceback.print_exc()
            failures.append(str(exc))
        QApplication.quit()

    def run(application):
        QTimer.singleShot(600, exercise)
        return original_exec()

    sys.argv = ['baofang-restart-check', '--live', '--runtime', str(runtime)]
    if phase == 'ax':
        sys.argv.append('--rekordbox-ax')
    from PySide6.QtCore import qInstallMessageHandler
    def message(kind, context, text):
        if 'qml' in text.lower() or 'referenceerror' in text.lower() or 'typeerror' in text.lower():
            warnings.append(text)
    previous = qInstallMessageHandler(message)
    try:
        with patch('app.main.Controller', CheckedController), patch.object(QApplication, 'exec', run):
            result = app_main()
    finally:
        qInstallMessageHandler(previous)
    assert not failures, failures
    assert not warnings, warnings
    return result


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        return child(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ.get('BAOFANG_UI_CHECK_DIR', root / 'runtime/live-restart-20261103')).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='restart-', dir=evidence) as temp:
        runtime = Path(temp)
        with (evidence / 'processes.log').open('w') as log:
            def launch(phase):
                return subprocess.Popen([sys.executable, '-m', 'tools.check_live_restart', '--child', phase,
                                         str(runtime), str(evidence)], cwd=root, stdout=log, stderr=log)
            seed = launch('seed')
            try:
                deadline = time.monotonic() + 20
                while not (runtime / 'ready').exists() and time.monotonic() < deadline:
                    assert seed.poll() is None, 'seed exited before ready; see processes.log'
                    time.sleep(.05)
                assert (runtime / 'ready').exists(), 'seed timed out'
                seed.kill()
                assert seed.wait(timeout=5) != 0
            finally:
                if seed.poll() is None:
                    seed.kill()
                    seed.wait(timeout=5)
            # Leave an old renderer snapshot to prove startup overwrites it.
            (runtime / 'program.json').write_bytes((evidence / 'old-program.json').read_bytes())
            for phase in ('restart', 'fresh', 'ax'):
                process = launch(phase)
                try:
                    assert process.wait(timeout=30) == 0, phase + ' failed; see processes.log'
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                print((evidence / f'{phase}-result.json').read_text())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
