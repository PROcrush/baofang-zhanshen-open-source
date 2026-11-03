"""Additional text-input, pixel-rendering and image boundary checks.

Isolated offscreen Qt windows and generated local fixtures only. Does not build
packages, operate DJ applications or change any real screen or user profile.
"""
import copy
import math
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QCoreApplication, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QInputMethodEvent, QPainter
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickView
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.controller import Controller
from app.screen_layers import POSITIONS, default_layers
from tools.check_screen_layers import find, wait_for
from tools.ui_state_smoke import click


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ.get('BAOFANG_UI_CHECK_DIR', root / 'runtime/screen-layers-deep-20261103')).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    results, warnings = [], []
    with tempfile.TemporaryDirectory(prefix='edges-', dir=evidence) as temp:
        folder = Path(temp)
        controller = Controller(root, folder / 'runtime')
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
        engine.rootContext().setContextProperty('controller', controller)
        engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        view = QQuickView()
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.setColor(Qt.GlobalColor.transparent)
        view.setSource(QUrl.fromLocalFile(str(root / 'ui/ScreenLayers.qml')))
        assert view.rootObject()
        view.show()
        stage = view.rootObject()
        text_item = next(item for item in stage.childItems() if item.objectName() == 'screenTextLayer')
        logo_item = next(item for item in stage.childItems() if item.objectName() == 'screenLogoImage')

        def press(name):
            window.requestActivate()
            item = find(window, name)
            assert item is not None and item.isEnabled(), name
            click(window, item)
            QTest.qWait(80)

        try:
            window.resize(1000, 700)
            assert wait_for(lambda: not controller.state['stale'])
            press('liveLayersButton')
            field = find(window, 'screenTextInput')
            field.setProperty('text', '')
            field.forceActiveFocus()
            preedit = QInputMethodEvent('欢迎', [])
            QCoreApplication.sendEvent(field, preedit)
            assert not controller.state['screenLayers']['text']['visible']
            commit = QInputMethodEvent()
            commit.setCommitString('欢迎大家')
            QCoreApplication.sendEvent(field, commit)
            assert field.property('text') == '欢迎大家'
            QTest.keyClick(window, Qt.Key.Key_Return)
            before = copy.deepcopy(controller.state['lyricOffsets'])
            for key in (Qt.Key.Key_T, Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight, Qt.Key.Key_Backslash):
                QTest.keyClick(window, key)
            assert '\n' in field.property('text')
            assert controller.state['lyricOffsets'] == before, 'typing triggered lyric synchronization keys'
            assert not controller.state['screenLayers']['text']['visible'], 'input/return unexpectedly went live'
            for value, enabled in (('🎧' * 300, True), ('🎧' * 301, False), (' \n ', False)):
                field.setProperty('text', value)
                QTest.qWait(30)
                button = find(window, 'screenTextApply')
                assert button.isEnabled() == enabled, (len(value), enabled, find(window, 'screenTextCount').property('text'), field.property('text')[:12])
                if len(value) == 300:
                    assert find(window, 'screenTextCount').property('text').startswith('300 /')
                    press('screenTextApply')
                    assert controller.state['screenLayers']['text']['text'] == value
            window.grabWindow().save(str(evidence / 'input-boundaries.png'))
            results.append('输入：中文预编辑/提交、回车换行不自动上屏，T / [ / ] / 反斜杠不触发同步；300 个 emoji 可上屏，301 个禁用')

            # Check actual pixels and image geometry, including transparent edges.
            fixture = QImage(240, 120, QImage.Format.Format_ARGB32)
            fixture.fill(QColor('transparent'))
            painter = QPainter(fixture)
            painter.fillRect(48, 24, 144, 72, QColor('#20DC60'))
            painter.end()
            png = folder / '透明.png'
            assert fixture.save(str(png))
            assert controller.chooseScreenLogo(str(png))
            owned = controller.state['logoImageUrl']
            view.resize(1920, 1080)
            for position in POSITIONS:
                for opacity in (1., .5, .1):
                    config = default_layers()
                    config['logo'].update(visible=True, size=20, position=position, opacity=opacity)
                    stage.setProperty('stateData', {'screenLayers': config, 'logoImageUrl': owned})
                    assert wait_for(logo_item.isVisible)
                    QTest.qWait(30)
                    image = view.grabWindow()
                    ratio = image.width() / view.width()
                    center = logo_item.mapToScene(logo_item.boundingRect().center())
                    pixel = image.pixelColor(round(center.x() * ratio), round(center.y() * ratio))
                    assert abs(pixel.alpha() - round(255 * opacity)) <= 3, (position, opacity, pixel.getRgb())
                    assert all(abs(actual - expected) <= 5 for actual, expected in zip(pixel.getRgb()[:3], (32, 220, 96)))
                    edge = logo_item.mapToScene(logo_item.boundingRect().topLeft())
                    assert image.pixelColor(round((edge.x()+3)*ratio), round((edge.y()+3)*ratio)).alpha() == 0
                    assert 0 <= logo_item.x() and logo_item.x()+logo_item.width() <= view.width()
                    assert 0 <= logo_item.y() and logo_item.y()+logo_item.height() <= view.height()
                    assert abs(logo_item.property('paintedWidth') / logo_item.property('paintedHeight') - 2) < .01
            view.grabWindow().save(str(evidence / 'logo-alpha.png'))
            results.append('Logo 像素：九个位置 × 100% / 50% / 10% 不透明度，实际 RGBA、透明边缘、宽高比及安全范围通过')

            cases = [('中文300', '唱' * 300), ('英文长串', 'W' * 300),
                     ('极多行', '\n'.join(['字'] * 150)), ('多空行', '首' + '\n' * 298 + '尾')]
            for width, height in ((640, 420), (1920, 1080), (3840, 2160)):
                view.resize(width, height)
                for label, value in cases:
                    for position in POSITIONS:
                        config = default_layers()
                        config['text'].update(visible=True, text=value, fontSize=120, position=position)
                        stage.setProperty('stateData', {'screenLayers': config})
                        QTest.qWait(20)
                        scale = text_item.scale()
                        assert text_item.property('paintedWidth') * scale <= text_item.width() + 1, (label, width, position)
                        assert text_item.property('paintedHeight') * scale <= text_item.height() + 1, (label, height, position)
                        assert text_item.property('text') == value
                        assert not text_item.property('truncated')
                        if label in ('极多行', '多空行'):
                            # Verify rendered pixels as well as layout sizes.
                            # This catches an incorrect scaling origin at the
                            # center/bottom even when painted size fits.
                            shot = view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
                            raw = shot.constBits()
                            stride = shot.bytesPerLine()
                            ink_rows = [row for row in range(shot.height())
                                        if any(raw[row*stride+3:(row+1)*stride:4])]
                            margin = stage.property('margin') * shot.height() / height
                            assert ink_rows and min(ink_rows) >= math.floor(margin) - 1, (label, width, position)
                            assert max(ink_rows) <= math.ceil(shot.height() - margin) + 1, (label, width, position)
                    if label == '中文300':
                        view.grabWindow().save(str(evidence / f'text-{width}x{height}.png'))
            results.append('文字边界：640×420 / 1080p / 4K，九个位置；300 中文、300 英文长串、150 行及大量空行均完整适配')

            press('screenLayersLogoTab')
            for suffix, format_name in (('jpg', 'JPEG'), ('webp', 'WEBP')):
                path = folder / ('DJ.' + suffix)
                assert fixture.save(str(path), format_name), format_name + ' fixture writer missing'
                assert controller.chooseScreenLogo(str(path)), controller.state['screenLayerNotice']
                decoded = QImage(QUrl(controller.state['logoImageUrl']).toLocalFile())
                assert not decoded.isNull() and (decoded.width(), decoded.height()) == (240, 120)
                if suffix == 'webp':
                    assert decoded.pixelColor(0, 0).alpha() == 0
            previous = copy.deepcopy(controller.state['screenLayers'])
            with patch('app.controller.QFileDialog.getOpenFileName', return_value=('', '')):
                press('screenLogoChoose')
            assert controller.state['screenLayers'] == previous
            invalid = folder / 'broken.png'
            invalid.write_bytes(b'not an image')
            wide = folder / 'too-wide.png'
            oversized = QImage(8193, 1, QImage.Format.Format_RGB32)
            oversized.fill(QColor('white'))
            assert oversized.save(str(wide))
            large = folder / 'large.png'
            with large.open('wb') as handle:
                handle.truncate(20_000_001)
            for path in (invalid, wide, large, folder / 'missing.png'):
                assert not controller.chooseScreenLogo(str(path)), path
                assert controller.state['screenLayers'] == previous
                assert controller.state['screenLayerNotice']
            results.append('图片：JPG / WebP 解码与透明度、取消选图；损坏、单边超限、20MB 超限和文件缺失均保留旧 Logo')
            assert not warnings, warnings
            assert not view.errors(), view.errors()
            results.append('QML 警告：0；仅隔离本机 Qt 验证，未打包、未测试外接电视或 DJ 软件')
        finally:
            controller.shutdown()
            window.hide()
            view.hide()
            engine.deleteLater()
    (evidence / 'report.txt').write_text('\n'.join(results) + '\n')
    print('\n'.join(results))
    print('evidence:', evidence)


if __name__ == '__main__':
    main()
