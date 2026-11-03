"""Real Qt check for the pre-show checklist (开场自检).

Uses the real Controller in replay mode with a temporary runtime and synthetic
WAV files; it never opens the guest screen, never reaches the network and
never reads keys. Screenshots go to BAOFANG_UI_CHECK_DIR (default
runtime/preshow-20261031).
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.controller import Controller
from app.source_routing import select_lyric_source
from tools.check_live_tools import seed_library
from tools.ui_state_smoke import click, visual_items

ROWS = ('dj', 'output', 'lyrics', 'deckA', 'deckB', 'setlist', 'midi', 'wish', 'cloud')


def find(window, name):
    return next((item for item in visual_items(window.contentItem()) if item.objectName() == name), None)


def wait_for(condition, ms=4000):
    for _ in range(ms // 50):
        if condition():
            return True
        QTest.qWait(50)
    return condition()


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root / 'runtime/preshow-20261031'
    evidence.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='baofang-preshow-', dir=evidence) as temp:
        folder = Path(temp) / 'runtime'
        folder.mkdir()
        tracks = seed_library(folder)
        application = QApplication([])
        application.setFont(QFont('PingFang SC', 13))
        QQuickStyle.setStyle('Basic')
        controller = Controller(root, folder)
        warnings = []
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
        engine.rootContext().setContextProperty('controller', controller)
        engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
        assert engine.rootObjects(), 'Main.qml failed to load'
        window = engine.rootObjects()[0]

        def press(name):
            node = find(window, name)
            assert node is not None and node.isVisible() and node.isEnabled(), name + ' is not reachable'
            click(window, node)
            QTest.qWait(150)

        def shown(name):
            node = find(window, name)
            return node is not None and node.isVisible()

        def dot(row_id):
            node = find(window, 'preshowDot-' + row_id)
            return node.property('color').name() if node is not None else ''

        def detail(row_id):
            node = find(window, 'preshowDetail-' + row_id)
            return node.property('text') if node is not None else ''

        try:
            window.resize(1000, 700)
            assert wait_for(lambda: not controller.state['stale']), 'replay did not start'
            toolbar = find(window, 'liveToolbar')
            button = find(window, 'livePreshowButton')
            assert button is not None and button.isVisible(), 'toolbar button missing'
            right = button.mapToScene(button.boundingRect().topRight()).x()
            assert right <= toolbar.mapToScene(toolbar.boundingRect().topRight()).x(), 'toolbar button clipped'
            controller.addToSetlist(tracks[0]['versionId'])
            controller.addToSetlist(tracks[1]['versionId'])

            press('livePreshowButton')
            assert wait_for(lambda: shown('preshowList')), 'dialog did not open'
            for row in ROWS:
                assert shown('preshowRow-' + row), row + ' row missing'
            theme_amber = dot('dj')
            assert '回放演示' in detail('dj'), detail('dj')
            assert detail('setlist') == '2 首 · 就绪 1 · 没歌词 1', detail('setlist')
            assert shown('preshowAction-setlist') and shown('preshowAction-output')
            assert find(window, 'preshowSummary').property('text'), 'summary empty'
            window.grabWindow().save(str(evidence / 'preshow-replay-1000x700.png'))
            results.append('打开：现场工具栏「开场自检」，9 项都在，回放模式标黄，歌单数对')

            # 清单每秒刷新，但内容不变时不能重建行，否则按钮会点不中
            find(window, 'preshowRow-dj').setProperty('probeMark', 7)
            QTest.qWait(2300)
            assert find(window, 'preshowRow-dj').property('probeMark') == 7, 'rows rebuilt without change'
            results.append('稳定：内容不变时 2.3 秒内不重建行')

            controller.clearOutput()
            assert wait_for(lambda: shown('preshowAction-lyrics'), 2500), 'hidden lyrics not flagged'
            assert dot('lyrics') == theme_amber
            press('preshowAction-lyrics')
            assert controller.state['selectedSource'] != 'none', controller.state['selectedSource']
            assert wait_for(lambda: not shown('preshowAction-lyrics'), 2500), 'restore action still shown'
            assert dot('lyrics') != theme_amber
            results.append('修复：隐藏歌词后标黄，点「恢复歌词」恢复并转绿')

            press('preshowAction-cloud')
            assert controller.state['offlineLibrary']['offlineOnly'], 'offline mode not on'
            assert wait_for(lambda: detail('cloud').startswith('已开'), 2500), detail('cloud')
            results.append('修复：点「打开离线演出」开启离线演出')

            press('preshowAction-setlist')
            assert wait_for(lambda: not shown('preshowList')), 'dialog did not close'
            assert window.property('currentPage') == 1, window.property('currentPage')
            results.append('跳转：歌单缺词点「去曲库」关闭弹窗并进入曲库')

            window.setProperty('currentPage', 0)
            QTest.qWait(200)
            press('appSettingsMenu')
            press('nav-preshow')
            assert wait_for(lambda: shown('preshowList')), 'menu entry did not open dialog'
            results.append('入口：设置与帮助菜单也能打开')

            window.resize(1440, 900)
            QTest.qWait(400)
            window.grabWindow().save(str(evidence / 'preshow-1440x900.png'))
            press('preshowDone')
            assert wait_for(lambda: not shown('preshowList')), 'done did not close'

            # Synthetic live state exercises the actual report -> dialog ->
            # action wiring. No DJ reader or guest HTTP service is started.
            controller.timer.stop()
            controller.stopBridge()
            selection = select_lyric_source('auto', -1, 'A', {side: {'ready': True} for side in 'AB'},
                                            video_fader_ready=False)
            controller._state.update(mode='serato', inputMode='serato', readerConnected=True,
                                      bridgeRunning=True, stale=False, selectedSource='auto',
                                      effectiveSource='none', selection=selection)
            controller.stateChanged.emit()
            press('livePreshowButton')
            assert wait_for(lambda: shown('preshowAction-lyrics'))
            assert dot('lyrics') == theme_amber
            assert 'A / B' in detail('lyrics')
            for width, height in ((1000, 700), (1440, 900)):
                window.resize(width, height)
                QTest.qWait(150)
                window.grabWindow().save(str(evidence / f'preshow-route-warning-{width}x{height}.png'))
            press('preshowAction-lyrics')
            assert not shown('preshowList') and window.property('currentPage') == 0
            assert shown('source-A') and shown('source-B')
            results.append('路由误报：自动路由不可用时歌词行标黄，查看来源返回现场可见 A/B 控件')

            controller._state['guestQr'] = {'running': True, 'url': 'http://127.0.0.1:5000/test-token',
                                             'image': '', 'onScreen': False}
            controller.stateChanged.emit()
            with patch('app.controller.lan_address', return_value='192.168.1.20'):
                press('livePreshowButton')
                assert wait_for(lambda: shown('preshowAction-wish'))
                assert dot('wish') == theme_amber and '关闭再打开' in detail('wish')
                for width, height in ((1000, 700), (1440, 900)):
                    window.resize(width, height)
                    QTest.qWait(150)
                    window.grabWindow().save(str(evidence / f'preshow-qr-warning-{width}x{height}.png'))
                press('preshowAction-wish')
                assert not shown('preshowList') and shown('wishServerToggle')
                assert find(window, 'wishServerToggle').property('checked')
                assert not controller.inbox.running, 'check unexpectedly started HTTP server'
            results.append('扫码误报：连接 Wi-Fi 后旧回环二维码仍标黄；按钮进入扫码设置且不自动重启服务')

            window.resize(1000, 700)
            QTest.qWait(300)
            window.grabWindow().save(str(evidence / 'live-toolbar-1000x700.png'))
            qml = [row for row in warnings if '.qml' in row]
            assert not qml, qml
            results.append('QML 警告：0')
        finally:
            controller.shutdown()
            window.close()
            engine.deleteLater()
    (evidence / 'report.txt').write_text('\n'.join(results) + '\n', encoding='utf-8')
    print('\n'.join(results))
    print('evidence:', evidence)


if __name__ == '__main__':
    main()
