"""Exercise the independent toolbox through real QML actions on generated files."""
import json
import os
import shutil
import subprocess
import time
import uuid
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
from app.toolbox import ToolboxController
from app.studio import file_identity, check_identity
from tools.ui_state_smoke import SmokeController, visual_items, click
from worker.studio_render import find_ffmpeg, probe_media


def main():
    root = Path(__file__).resolve().parent.parent
    target = root / 'runtime/ui-redesign-20261012/toolbox-ui'
    folder = target / uuid.uuid4().hex
    media = folder / 'media'
    media.mkdir(parents=True)
    video = media / 'generated.mp4'
    subprocess.run([find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-n',
                    '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=24:d=1',
                    '-vf', 'drawbox=x=20:y=135:w=260:h=25:color=white:t=fill',
                    '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)],
                   check=True, timeout=30)
    shutil.copyfile(video, media / 'duplicate.mp4')
    source = media / 'source.srt'
    source.write_text('1\n00:00:00,000 --> 00:00:00,800\n这一刻声音让我们靠近\n', encoding='utf-8')
    destination = media / 'target.srt'
    destination.write_text('1\n00:00:00,100 --> 00:00:00,900\n这一刻声音让我们靠近\n', encoding='utf-8')
    identities = [file_identity(path) for path in media.iterdir()]
    application = QApplication.instance() or QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    toolbox = ToolboxController(root, folder / 'runtime')
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
    facade = SmokeController('replay')
    engine.rootContext().setContextProperty('controller', facade)
    engine.rootContext().setContextProperty('studioTools', toolbox)
    engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setProperty('currentPage', 6)
    QTest.qWait(100)

    def idle():
        deadline = time.monotonic() + 30
        while toolbox.state['busy'] and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not toolbox.state['busy'], toolbox.state['status']
        assert not toolbox.state['error'], toolbox.state['status']

    def press(label):
        for item in visual_items(window.contentItem()):
            if item.isVisible() and item.isEnabled() and item.property('text') == label:
                click(window, item)
                QTest.qWait(50)
                return
        raise AssertionError('Missing enabled action: ' + label)

    toolbox.loadFolder(str(media)); idle()
    assert len(toolbox.state['media']) == 4
    press('检查重复内容'); idle()
    assert len(toolbox.state['duplicates']) == 1
    press('视频字幕修补')
    toolbox.loadVideo(str(video)); idle()
    assert (toolbox.state['videoWidth'], toolbox.state['videoHeight']) == (320, 180)
    for key, value in {'x':19, 'y':134, 'width':262, 'height':27}.items():
        toolbox.setROI(key, value)
    press('生成去字幕样片'); idle()
    result = Path(toolbox.state['resultPath'])
    assert result.is_file()
    assert any(row['codec_type'] == 'video' for row in probe_media(result, include_video=True)['streams'])
    subprocess.run([find_ffmpeg(), '-v', 'error', '-i', str(result), '-f', 'null', '-'], check=True, timeout=30)
    press('演唱会映射')
    toolbox.loadLyrics('source', str(source))
    toolbox.loadLyrics('target', str(destination))
    press('生成段落映射'); idle()
    assert toolbox.state['pairingRows'] and Path(toolbox.state['mappingPath']).is_file()
    with patch('app.toolbox.QDesktopServices.openUrl', return_value=True) as opened:
        press('查看结果')
        assert opened.call_args.args[0].toLocalFile() == toolbox.state['mappingPath']
        press('视频字幕修补')
        press('查看结果')
        assert opened.call_args.args[0].toLocalFile() == str(result)
    press('演唱会映射')
    for identity in identities:
        check_identity(identity)
    QTest.qWait(100)
    assert not warnings, '\n'.join(warnings)
    assert window.grabWindow().save(str(folder / 'mapping.png'))
    report = {'passed':True, 'syntheticMedia':True, 'originalsUnchanged':True,
              'qmlWarnings':warnings, 'checks':['actual duplicate action', 'silent-video metadata',
              'actual subtitle-repair action and full output decode', 'actual pairing action',
              'result action opens the active tool output'],
              'previewPath':str(result), 'mappingPath':toolbox.state['mappingPath']}
    target.mkdir(parents=True, exist_ok=True)
    (target / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    window.close()
    toolbox.shutdown()
    engine.deleteLater()
    QTest.qWait(30)


if __name__ == '__main__':
    main()
