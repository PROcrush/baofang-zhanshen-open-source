"""Real Qt check for the nonmodal DJ guest-screen content workflow.

Isolated runtime, synthetic picture and replay only; no DJ apps, permissions,
network, packaging or real external display. Writes UI evidence locally.
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QCoreApplication, QMetaObject, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QInputMethodEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.controller import Controller, FileController
from tools.check_screen_layers import find, wait_for
from tools.ui_state_smoke import click, visual_items


def variant(value):
    return value.toVariant() if hasattr(value, 'toVariant') else value


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ.get('BAOFANG_UI_CHECK_DIR', root / 'runtime/screen-content-20261103')).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    warnings, results = [], []

    def load(controller, qml):
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
        engine.rootContext().setContextProperty('controller', controller)
        engine.load(QUrl.fromLocalFile(str(root / 'ui' / qml)))
        assert engine.rootObjects(), qml + ' failed'
        return engine, engine.rootObjects()[0]

    with tempfile.TemporaryDirectory(prefix='dj-content-', dir=evidence) as temp:
        runtime = Path(temp) / 'runtime'
        runtime.mkdir()
        controller = Controller(root, runtime)
        renderer = FileController(root, runtime)
        engine, window = load(controller, 'Main.qml')
        output_engine, output = load(renderer, 'Program.qml')

        def press(name):
            window.requestActivate()
            item = find(window, name)
            assert item is not None and item.isEnabled(), name + ' unavailable'
            point = item.mapToScene(item.boundingRect().center())
            assert 0 < point.x() < window.width() and 0 < point.y() < window.height(), (name, point)
            click(window, item)
            QTest.qWait(100)

        def shown(name):
            return find(window, name) is not None

        def preview():
            return variant(find(window, 'programView').property('stateData'))

        def reveal(name):
            item = find(window, name)
            scroll = find(window, 'screenContentScroll')
            flickable = scroll.property('contentItem')
            center = item.mapToItem(flickable, item.boundingRect().center())
            target = flickable.property('contentY') + center.y() - flickable.height()/2
            limit = max(0, flickable.property('contentHeight') - flickable.height())
            flickable.setProperty('contentY', max(0, min(limit, target)))
            QTest.qWait(80)

        try:
            window.resize(1000, 700)
            assert wait_for(lambda: not controller.state['stale'])
            press('liveLayersButton')
            for name in ('source-A', 'source-B', 'clearOutputButton', 'liveTab-sync'):
                assert shown(name)
            assert not any(item.objectName() == 'outputLayersPopup' for item in visual_items(window.contentItem()))
            press('screenPhrase-生日')
            assert not find(window, 'screenTextApply').isEnabled(), 'unfilled name can go live'
            field = find(window, 'screenTextInput')
            field.setProperty('text', '祝李先生生日快乐！')
            QTest.qWait(80)
            assert preview()['screenLayers']['text']['text'] == '祝李先生生日快乐！'
            assert not controller.state['screenLayers']['text']['visible']
            assert not renderer.state['screenLayers']['text']['visible'], 'draft leaked to separate output'
            assert shown('screenLyricSafeGuide')
            window.grabWindow().save(str(evidence / '01-draft-1000x700.png'))
            press('source-B')
            assert controller.state['selectedSource'] == 'B', 'draft blocked source takeover'
            press('liveTab-sync')
            press('liveLayersButton')
            assert find(window, 'screenTextInput').property('text') == '祝李先生生日快乐！', 'leaving panel lost draft'
            press('screenTextApply')
            assert controller.state['screenLayers']['text']['visible']
            assert 0 < controller.state['screenTextRemaining'] <= 15
            assert find(output, 'screenTextLayer')
            assert not shown('screenLyricSafeGuide'), 'draft guide reached live view'
            assert preview()['screenLayers']['text']['text'] == '祝李先生生日快乐！'
            assert not json.loads((runtime / 'screen-layers.json').read_text())['layers']['text']['visible']
            field.setProperty('text', '这是下一条，尚未上屏')
            QTest.qWait(70)
            assert controller.state['screenLayers']['text']['text'] == '祝李先生生日快乐！'
            press('screenPreviewLive')
            assert preview()['screenLayers']['text']['text'] == '祝李先生生日快乐！'
            press('screenPreviewDraft')
            assert preview()['screenLayers']['text']['text'] == '这是下一条，尚未上屏'
            window.grabWindow().save(str(evidence / '02-live-and-draft.png'))
            press('screenTextToggle')
            assert not controller.state['screenLayers']['text']['visible']
            assert find(window, 'screenTextInput').property('text') == '这是下一条，尚未上屏'
            results.append('现场喊话：模板填名校验、待上屏预览隔离、15 秒倒计时、现场/草稿切换、撤下与跨标签保留草稿；A/B 接管可操作')

            # Open and use the actual advanced controls in the scroll viewport.
            reveal('screenAdjustToggle')
            press('screenAdjustToggle')
            reveal('screenLayerPosition')
            press('screenLayerPosition')
            QTest.keyClick(window, Qt.Key.Key_End)
            QTest.keyClick(window, Qt.Key.Key_Return)
            QTest.qWait(80)
            assert preview()['screenLayers']['text']['position'] == 'bottom-right'
            reveal('screenLayerSize')
            size = find(window, 'screenLayerSize')
            size.forceActiveFocus()
            QTest.keyClick(window, Qt.Key.Key_Right)
            QTest.qWait(50)
            assert preview()['screenLayers']['text']['fontSize'] == 41
            reveal('screenLayerOpacity')
            opacity = find(window, 'screenLayerOpacity')
            opacity.forceActiveFocus()
            QTest.keyClick(window, Qt.Key.Key_Left)
            QTest.qWait(50)
            assert preview()['screenLayers']['text']['opacity'] == .99
            reveal('screenTextColor')
            color = find(window, 'screenTextColor')
            color.forceActiveFocus()
            QMetaObject.invokeMethod(color, 'selectAll')
            change = QInputMethodEvent()
            change.setCommitString('#FFD54F')
            QCoreApplication.sendEvent(color, change)
            QTest.qWait(80)
            assert preview()['screenLayers']['text']['color'] == '#FFD54F'
            assert controller.state['screenLayers']['text']['position'] == 'top-center'
            results.append('小窗口展开细调：滚动、九宫格选择、字号/不透明度键盘调节与字色输入均只更新待上屏预览')

            # A full draft update (layout included) is committed only once.
            panel = find(window, 'screenContentPanel')
            draft = variant(panel.property('draftText'))
            draft.update(position='top-center', fontSize=52, color='#FFD54F', opacity=.6)
            panel.setProperty('draftText', draft)
            before = copy.deepcopy(controller.state['screenLayers'])
            with patch('app.controller.save_layers', side_effect=OSError('模拟磁盘满')):
                press('screenTextApply')
            assert controller.state['screenLayers'] == before and shown('screenLayersNotice')
            press('screenTextApply')
            assert controller.state['screenLayers']['text']['color'] == '#FFD54F'
            assert controller.state['screenLayers']['text']['fontSize'] == 52
            assert controller.state['screenLayers']['text']['opacity'] == .6
            results.append('文字与布局整体提交；保存失败保留旧画面和草稿，重试成功后清除错误')

            reveal('screenTextDuration')
            press('screenTextDuration')
            QTest.keyClick(window, Qt.Key.Key_End)
            QTest.keyClick(window, Qt.Key.Key_Return)
            QTest.qWait(60)
            field.forceActiveFocus()
            QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.MetaModifier if sys.platform == 'darwin' else Qt.KeyboardModifier.ControlModifier)
            QTest.qWait(100)
            assert controller.state['screenTextValidUntilNs'] == 0
            assert controller.state['screenTextRemaining'] == 0
            assert json.loads((runtime/'screen-layers.json').read_text())['layers']['text']['visible']
            reveal('screenTextDuration')
            press('screenTextDuration')
            QTest.keyClick(window, Qt.Key.Key_Home)
            QTest.keyClick(window, Qt.Key.Key_Return)
            results.append('固定显示需明确选择，输入内发布快捷键生效并持久保存；普通回车仍由输入边界检查验证换行')

            press('screenLayersLogoTab')
            logo_path = Path(temp) / 'DJ透明.png'
            logo = QImage(240, 120, QImage.Format.Format_ARGB32)
            logo.fill(QColor('transparent'))
            for x in range(60, 180):
                for y in range(30, 90):
                    logo.setPixelColor(x, y, QColor('#20DC60'))
            assert logo.save(str(logo_path))
            before = copy.deepcopy(controller.state['screenLayers'])
            with patch('app.controller.QFileDialog.getOpenFileName', return_value=(str(logo_path), '')):
                press('screenLogoChoose')
            assert controller.state['screenLayers'] == before, 'import automatically published'
            assert preview()['screenLayers']['logo']['image']
            assert not renderer.state['screenLayers']['logo']['visible']
            assert wait_for(lambda: find(window, 'screenLogoImage') is not None)
            window.grabWindow().save(str(evidence / '03-logo-preview.png'))
            press('screenLogoApply')
            assert wait_for(lambda: find(output, 'screenLogoImage') is not None)
            logo_path.unlink()
            assert Path(QUrl(controller.state['logoImageUrl']).toLocalFile()).exists()
            press('screenLogoToggle')
            assert not controller.state['screenLayers']['logo']['visible']
            press('screenLogoApply')
            assert controller.state['screenLayers']['logo']['visible']
            for width, height in ((1000, 700), (1440, 900)):
                window.resize(width, height)
                QTest.qWait(150)
                for name in ('source-A', 'source-B', 'screenLogoApply', 'screenLogoToggle', 'screenExtrasHide'):
                    node = find(window, name)
                    assert node
                    point = node.mapToScene(node.boundingRect().center())
                    assert 0 < point.x() < width and 0 < point.y() < height, (name, point)
                window.grabWindow().save(str(evidence / f'04-logo-live-{width}x{height}.png'))
            results.append('DJ 标识：导入仅本机预览，明确应用后才上屏；单独隐藏/显示，原图移走仍显示，两种窗口尺寸关键按钮可达')

            # Approval and manual shouting replace one another, not stack.
            controller._state['guestInbox'] = [{'id': 'qa', 'name': '阿杰', 'text': '生日快乐', 'time': '测试'}]
            controller.tick()
            press('screenWishesTab')
            assert not controller.state['guestMessage']['visible']
            press('wishApprove-qa')
            assert controller.state['guestMessage'] == {'visible': True, 'text': '阿杰：生日快乐'}
            assert not controller.state['screenLayers']['text']['visible']
            assert not controller.state['guestInbox']
            press('screenLayersTextTab')
            field = find(window, 'screenTextInput')
            field.setProperty('text', '切回 DJ 喊话')
            press('screenTextApply')
            assert not controller.state['guestMessage']['visible']
            controller._state['guestQr'] = {'running': True, 'onScreen': True, 'url': '', 'image': ''}
            controller._state['guestInbox'] = [{'id': 'keep', 'text': '待审保留'}]
            controller.tick()
            selected = controller.state['selectedSource']
            press('screenExtrasHide')
            assert not controller.state['screenLayers']['text']['visible']
            assert not controller.state['screenLayers']['logo']['visible']
            assert not controller.state['guestQr']['onScreen']
            assert controller.state['guestQr']['running']
            assert controller.state['guestInbox'][0]['id'] == 'keep'
            assert controller.state['selectedSource'] == selected
            results.append('祝福手动审核、一次一条；与喊话相互替换；全部撤下覆盖文字/Logo/祝福/二维码，保留待审内容、素材及歌词来源')

            # Both controllers enforce expiry, even with a frozen old snapshot.
            controller._state['guestQr'] = {'running': False, 'onScreen': False, 'url': '', 'image': ''}
            row = dict(controller.state['screenLayers']['text'], text='一秒后撤下')
            assert controller.applyScreenContent('text', row, 1)
            assert wait_for(lambda: renderer.state['screenLayers']['text']['visible'] and renderer.state['screenLayers']['text']['text'] == '一秒后撤下')
            controller.timer.stop()
            assert wait_for(lambda: not renderer.state['screenLayers']['text']['visible'], 2500)
            assert not renderer.state['screenTextRemaining']
            restored = Controller(root, runtime)
            try:
                assert not restored.state['screenLayers']['text']['visible']
                assert restored.state['screenLayers']['text']['text'] == '一秒后撤下'
                assert restored.state['screenLayers']['logo']['image']
                assert not restored.state['guestMessage']['visible']
            finally:
                restored.shutdown()
            results.append('冻结主程序快照后独立客屏仍按期限撤下；新主程序保留文字草稿和图片，不重放旧临时喊话/祝福')
            assert not warnings, warnings
            results.append('QML 警告：0；隔离源码验证，未打包、未实测外接电视或 DJ 软件')
        finally:
            renderer.timer.stop()
            controller.shutdown()
            window.hide()
            output.hide()
            engine.deleteLater()
            output_engine.deleteLater()
    (evidence / 'report.txt').write_text('\n'.join(results)+'\n')
    print('\n'.join(results))
    print('evidence:', evidence)


if __name__ == '__main__':
    main()
