"""Qt acceptance for personal phrases, sent history and portable DJ profiles.

Uses temporary data and a generated local image, never real songs or accounts.
"""
import copy
import json
import os
import tempfile
import zipfile
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
from app.controller import Controller, FileController
from tools.check_screen_layers import find, wait_for
from tools.ui_state_smoke import click


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ.get('BAOFANG_UI_CHECK_DIR', root/'runtime/screen-library-20261103')).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC',13))
    QQuickStyle.setStyle('Basic')
    results, warnings = [], []
    with tempfile.TemporaryDirectory(prefix='personal-',dir=evidence) as temp:
        runtime = Path(temp)/'runtime'
        runtime.mkdir()
        controller = Controller(root,runtime)
        renderer = FileController(root,runtime)
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
        engine.rootContext().setContextProperty('controller',controller)
        engine.load(QUrl.fromLocalFile(str(root/'ui/Main.qml')))
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        window.resize(1000,700)

        def press(name):
            window.requestActivate()
            item = find(window,name)
            assert item and item.isEnabled(), name
            scroll = find(window,'screenContentScroll')
            if scroll:
                flick = scroll.property('contentItem')
                parent = item.parentItem()
                inside = False
                while parent:
                    if parent == flick:
                        inside = True
                        break
                    parent = parent.parentItem()
                if inside:
                    center = item.mapToItem(flick,item.boundingRect().center())
                    top = flick.property('contentY')+center.y()-flick.height()/2
                    limit = max(0,flick.property('contentHeight')-flick.height())
                    flick.setProperty('contentY',max(0,min(limit,top)))
                    QTest.qWait(60)
            point = item.mapToScene(item.boundingRect().center())
            assert 0 < point.x() < window.width() and 0 < point.y() < window.height(),(name,point)
            click(window,item)
            QTest.qWait(100)

        try:
            assert wait_for(lambda:not controller.state['stale'])
            press('liveLayersButton')
            press('screenPersonalTab')
            find(window,'screenPhraseLabel').setProperty('text','我的欢迎')
            find(window,'screenPhraseBody').setProperty('text','欢迎{姓名}，今晚一起唱！')
            press('screenPhraseSave')
            phrase = controller.state['screenContentLibrary']['phrases'][0]
            assert phrase['label']=='我的欢迎'
            assert not controller.state['screenLayers']['text']['visible']
            window.grabWindow().save(str(evidence/'01-personal-phrases.png'))
            press('screenLayersTextTab')
            button = find(window,'screenPhrase-我的欢迎')
            button.setProperty('probeMark',17)
            QTest.qWait(1100)
            assert find(window,'screenPhrase-我的欢迎').property('probeMark')==17,'heartbeat rebuilt phrase buttons'
            press('screenPhrase-我的欢迎')
            assert not find(window,'screenTextApply').isEnabled()
            person = find(window,'screenPhrasePerson')
            assert person
            person.setProperty('text','陈先生')
            QTest.qWait(60)
            assert find(window,'screenTextInput').property('text')=='欢迎陈先生，今晚一起唱！'
            assert not renderer.state['screenLayers']['text']['visible']
            window.grabWindow().save(str(evidence/'02-fill-name-preview.png'))
            press('screenTextApply')
            assert controller.state['screenLayers']['text']['text']=='欢迎陈先生，今晚一起唱！'
            history = controller.state['screenContentLibrary']['history']
            assert len(history)==1 and history[0]['row']['text']=='欢迎陈先生，今晚一起唱！'
            assert 'screenContentLibrary' not in json.loads((runtime/'program.json').read_text())['state']
            press('screenTextToggle')
            press('screenPersonalTab')
            press('screenPersonalHistory')
            window.grabWindow().save(str(evidence/'03-sent-history.png'))
            press('screenHistoryUse-'+history[0]['id'])
            assert not controller.state['screenLayers']['text']['visible'],'history replayed without confirmation'
            find(window,'screenTextInput').setProperty('text','欢迎王女士，今晚一起唱！')
            press('screenTextApply')
            assert len(controller.state['screenContentLibrary']['history'])==2
            results.append('常用语新增不发出，姓名变量必填后预览发布；发送历史填回草稿、改名再发；心跳刷新不重建常用语按钮')

            picture = Path(temp)/'DJ.png'
            image = QImage(160,80,QImage.Format.Format_ARGB32)
            image.fill(QColor('#20DC60'))
            assert image.save(str(picture))
            press('screenLayersLogoTab')
            with patch('app.controller.QFileDialog.getOpenFileName',return_value=(str(picture),'')):
                press('screenLogoChoose')
            press('screenLogoApply')
            press('screenPersonalTab')
            press('screenPersonalProfile')
            bundle = Path(temp)/'我的DJ.bfdj.zip'
            with patch('app.controller.QFileDialog.getSaveFileName',return_value=(str(bundle),'')):
                press('screenProfileExport')
            assert bundle.is_file()
            with zipfile.ZipFile(bundle) as archive:
                manifest = json.loads(archive.read('profile.json'))
                assert manifest['layers']['text']['text']==''
                assert 'history' not in manifest
                assert '王女士' not in archive.read('profile.json').decode()
                assert set(archive.namelist())=={'profile.json','logo.png'}
            assert controller.saveScreenPhrase('','另一场','暂不采用')
            assert controller.setScreenText('当前正在显示，不要提前覆盖')
            before = copy.deepcopy(controller.state['screenLayers'])
            library = copy.deepcopy(controller.state['screenContentLibrary'])
            with patch('app.controller.QFileDialog.getOpenFileName',return_value=(str(bundle),'')):
                press('screenProfileImport')
            assert controller.state['screenLayers']==before
            assert controller.state['screenContentLibrary']==library
            window.grabWindow().save(str(evidence/'04-import-preview-1000x700.png'))
            press('screenProfileCancel')
            assert controller.state['screenLayers']==before
            with patch('app.controller.QFileDialog.getOpenFileName',return_value=(str(bundle),'')):
                press('screenProfileImport')
            with patch('app.controller.save_profile',side_effect=OSError('模拟只读')):
                press('screenProfileApply')
            assert controller.state['screenLayers']==before
            assert controller.state['screenContentLibrary']==library
            press('screenProfileApply')
            assert not controller.state['screenLayers']['text']['visible']
            assert controller.state['screenLayers']['logo']['visible']
            assert len(controller.state['screenContentLibrary']['phrases'])==5
            assert controller.state['screenContentLibrary']['history']==library['history']
            results.append('配置导出只带 Logo/布局/常用语；导入与取消不改现场，采用失败回滚、重试后一起替换配置且保留本机历史')
            for width,height in ((1000,700),(1440,900)):
                window.resize(width,height)
                QTest.qWait(100)
                for name in ('source-A','source-B','clearOutputButton','screenExtrasHide'):
                    node = find(window,name)
                    assert node
                    center = node.mapToScene(node.boundingRect().center())
                    assert 0 < center.x() < width and 0 < center.y() < height
                window.grabWindow().save(str(evidence/f'05-config-applied-{width}x{height}.png'))
            controller.shutdown()
            restored = Controller(root,runtime)
            try:
                assert restored.state['screenContentLibrary']==controller.state['screenContentLibrary']
                assert restored.state['screenLayers']['logo']['visible']
                assert not restored.state['screenLayers']['text']['visible']
            finally:
                restored.shutdown()
            assert not warnings,warnings
            results.append('重启恢复常用语/最近历史和已采用标识；两种窗口接管/撤下可达，QML 警告 0；未打包或操作真实 DJ 软件')
        finally:
            renderer.timer.stop()
            controller.shutdown()
            window.hide()
            engine.deleteLater()
    (evidence/'report.txt').write_text('\n'.join(results)+'\n')
    print('\n'.join(results))
    print('evidence:',evidence)


if __name__=='__main__':
    main()
