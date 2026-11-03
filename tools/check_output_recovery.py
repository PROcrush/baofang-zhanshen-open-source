"""Real Qt and subprocess recovery check, with isolated runtime and no DJ/network.

Starts the actual renderer, kills it, checks bounded retries and explicit closure.
Screenshots and logs stay under BAOFANG_UI_CHECK_DIR.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QFont, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.controller import Controller
from tools.check_preshow import find, wait_for
from tools.ui_state_smoke import click


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ.get('BAOFANG_UI_CHECK_DIR', root / 'runtime/output-recovery-20261102')).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    results, warnings, children = [], [], []
    real_popen = subprocess.Popen
    launch_mode = 'renderer'

    def launch(arguments, **kwargs):
        if launch_mode == 'fail':
            arguments = [sys.executable, '-c', 'raise SystemExit(2)']
        elif launch_mode == 'close':
            # Exercise Program.qml onClosing -> FileController.closeOutput -> exit 0.
            driver = ('from PySide6.QtCore import QTimer; '
                      'from PySide6.QtGui import QGuiApplication; '
                      'from PySide6.QtWidgets import QApplication; '
                      'from app.main import main; '
                      'original=QApplication.exec; '
                      'QApplication.exec=lambda self: (QTimer.singleShot(500, '
                      'lambda: QGuiApplication.topLevelWindows()[0].close()), original())[1]; '
                      'raise SystemExit(main())')
            arguments = [sys.executable, '-c', driver, *arguments[3:]]
        else:
            capture = evidence / f'renderer-{len(children)}.png'
            capture.unlink(missing_ok=True)
            arguments = [*arguments, '--screenshot', str(capture)]
        process = real_popen(arguments, **kwargs)
        children.append(process)
        return process

    with tempfile.TemporaryDirectory(prefix='baofang-output-', dir=evidence) as temp:
        controller = Controller(root, Path(temp))
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
        engine.rootContext().setContextProperty('controller', controller)
        engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
        assert engine.rootObjects(), 'Main.qml failed to load'
        window = engine.rootObjects()[0]

        def phase():
            return controller.state['outputRecovery']['phase']

        def captured(index):
            path = evidence / f'renderer-{index}.png'
            return path.exists() and path.read_bytes().endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82')

        def press(name):
            node = find(window, name)
            assert node is not None and node.isVisible() and node.isEnabled(), name
            click(window, node)
            QTest.qWait(60)

        def screenshot(name):
            for width, height in ((1000, 700), (1440, 900)):
                window.resize(width, height)
                QTest.qWait(120)
                assert find(window, 'liveOutputStatus').isVisible()
                window.grabWindow().save(str(evidence / f'{name}-{width}x{height}.png'))

        try:
            with patch('app.controller.subprocess.Popen', side_effect=launch):
                assert wait_for(lambda: not controller.state['stale'])
                controller.selectSource('B')
                controller.setLyricOffset(250)
                controller.clearOutput()
                controller.setOutputTarget(application.primaryScreen().name(), False)
                logo_path = Path(temp) / 'recovery-logo.png'
                logo = QImage(240, 120, QImage.Format.Format_ARGB32)
                logo.fill(QColor('#20DC60'))
                assert logo.save(str(logo_path))
                assert controller.chooseScreenLogo(str(logo_path))
                assert controller.setScreenText('DJ RECOVERY\n客屏图层恢复验证')
                preserved = {key: controller.state[key] for key in (
                    'selectedSource', 'resumeSource', 'lyricOffsets', 'style', 'outputTarget', 'screenLayers')}
                press('liveOutputButton')
                assert wait_for(lambda: captured(0), 8000), 'renderer did not capture'
                assert controller.output.poll() is None
                original = controller.output
                original.kill()
                assert wait_for(lambda: phase() == 'waiting'), 'exit not detected'
                assert find(window, 'liveOutputStatus').property('text') == '输出正在重开'
                assert find(window, 'liveOutputButton').property('text') == '关闭客屏'
                screenshot('waiting')
                assert wait_for(lambda: controller.output is not None and controller.output.pid != original.pid)
                assert wait_for(lambda: captured(1), 8000), 'restarted renderer did not capture'
                assert phase() == 'recovered' and controller.output.poll() is None
                payload = json.loads((Path(temp) / 'program.json').read_text())
                for key, value in preserved.items():
                    assert payload['state'][key] == value, key + ' changed during restart'
                assert not payload['state']['lyric']['visible'], 'hidden lyrics reappeared'
                first_image = QImage(str(evidence / 'renderer-0.png'))
                recovered_image = QImage(str(evidence / 'renderer-1.png'))
                assert not first_image.isNull() and first_image.size() == recovered_image.size()
                # Compare the actual center of the default top-left Logo and
                # the central text region, independently of source/lyric state.
                margin = min(first_image.width(), first_image.height()) * .045
                logo_x = round(margin + first_image.width() * .18 / 2)
                logo_y = round(margin + first_image.width() * .18 / 4)
                assert first_image.pixelColor(logo_x, logo_y).name() == '#20dc60'
                assert recovered_image.pixelColor(logo_x, logo_y).name() == '#20dc60'
                text_rect = (round(first_image.width()*.15), round(first_image.height()*.3),
                             round(first_image.width()*.7), round(first_image.height()*.4))
                assert first_image.copy(*text_rect) == recovered_image.copy(*text_rect), 'text pixels changed after recovery'
                white_pixels = sum(1 for y in range(text_rect[1], text_rect[1]+text_rect[3], 3)
                                   for x in range(text_rect[0], text_rect[0]+text_rect[2], 3)
                                   if min(first_image.pixelColor(x,y).getRgb()[:3]) > 220)
                assert white_pixels > 30, 'recovery screenshot is missing the actual text'
                screenshot('recovered')
                results.append('真实客屏进程被终止后重开；保留隐藏、B 盘、250ms 偏移、样式、显示器与图层；Logo 及文字画面像素恢复一致')

                controller.output.kill()
                assert wait_for(lambda: phase() == 'waiting')
                count = len(children)
                press('liveOutputButton')
                QTest.qWait(2300)
                assert len(children) == count and not controller.state['outputRequested']
                results.append('等待重开时点关闭客屏，取消重试且不重开')

                launch_mode = 'close'
                press('liveOutputButton')
                closing = children[-1]
                assert wait_for(lambda: closing.poll() is not None, 8000)
                assert closing.returncode == 0, 'window closure did not exit cleanly'
                assert wait_for(lambda: not controller.state['outputRequested'])
                count = len(children)
                QTest.qWait(1200)
                assert len(children) == count
                results.append('真实 Program.qml 窗口主动关闭，退出码 0，不自动重开')

                launch_mode = 'fail'
                count = len(children)
                press('liveOutputButton')
                assert wait_for(lambda: phase() == 'failed', 14000), 'retry limit was not reached'
                assert len(children) == count + 4, 'initial launch plus exactly three retries required'
                assert find(window, 'liveOutputStatus').property('text') == '输出恢复失败'
                press('livePreshowButton')
                assert wait_for(lambda: find(window, 'preshowAction-output') is not None)
                assert find(window, 'preshowAction-output').property('text') == '重新打开'
                window.grabWindow().save(str(evidence / 'failed-preshow.png'))
                press('preshowDone')
                screenshot('failed')
                results.append('真实失败子进程：1/2/4 秒退避，最多 3 次后停止；现场和开场自检显示失败')

                launch_mode = 'renderer'
                press('liveOutputButton')
                last = len(children) - 1
                assert wait_for(lambda: captured(last), 8000)
                controller.output.kill()
                assert wait_for(lambda: phase() == 'waiting')
                count = len(children)
                controller.shutdown()
                QTest.qWait(1300)
                assert len(children) == count
                results.append('手动可重新打开；退出主程序时取消待重试，不遗留输出进程')
        finally:
            if not controller.shutting_down:
                controller.shutdown()
            for process in children:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)
            (evidence / 'renderer.log').write_text((Path(temp) / 'renderer.log').read_text(), encoding='utf-8')
            window.close()
            engine.deleteLater()
        assert not warnings, warnings
        renderer_log = (evidence / 'renderer.log').read_text()
        assert '.qml:' not in renderer_log, renderer_log
        results.append('QML 警告 0；1000×700 / 1440×900 截图已保存；未接真实 DJ / 电视')
    (evidence / 'report.txt').write_text('\n'.join(results) + '\n', encoding='utf-8')
    print('\n'.join(results))
    print('evidence:', evidence)


if __name__ == '__main__':
    main()
