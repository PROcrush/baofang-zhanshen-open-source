"""Exercise the real editor transport and audio clock without rendering a movie."""
import json
import os
import struct
import tempfile
import wave
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPoint, QPointF, QSize, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.studio import StudioController
from app.studio_preview import StudioPreviewProvider
from tools.ui_state_smoke import named_items, click
from tests.test_studio import wait_idle


def main():
    application = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle('Basic')
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        audio = folder / '实时预览 # 测试.wav'
        with wave.open(str(audio), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(48000)
            stream.writeframes(struct.pack('<h', 0) * 48000 * 5)
        subtitle = folder / '歌词.srt'
        subtitle.write_text('1\n00:00:00,100 --> 00:00:02,000\n第一句同步歌词\n\n2\n00:00:02,000 --> 00:00:04,900\n第二句同步歌词\n')
        studio = StudioController(root, folder / 'runtime')
        studio.chooseAudio(str(audio)); wait_idle(studio)
        studio.chooseSubtitles(str(subtitle)); wait_idle(studio)
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
        engine.rootContext().setContextProperty('studio', studio)
        provider = StudioPreviewProvider(studio)
        engine.addImageProvider('studioPreview', provider)
        engine.loadData(('import QtQuick\nimport QtQuick.Controls\nimport "' + QUrl.fromLocalFile(str(root / 'ui')).toString() + '"\nApplicationWindow { visible: true; width: 1440; height: 900; StudioPanel { anchors.fill: parent } }').encode())
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        named_items(window)['studioWorkspace'].setProperty('advancedEditing', True)
        QTest.qWait(200)
        try:
            items = named_items(window)
            transport = items.get('studioPlayPreview')
            assert transport and transport.isVisible(), '编辑页缺少实时播放按钮，只能先渲染完整影片'
            studio.seek(300)
            project = folder / 'project.bfstudio.json'
            studio.saveProject(str(project))
            click(window, transport)
            QTest.qWait(650)
            assert studio.state['playbackPlaying'], studio.state
            assert studio.state['positionMs'] > 700, '点击播放后歌曲时钟没有前进'
            assert studio.state['currentText'] == '第一句同步歌词'
            assert not studio.state['resultPath'] and not studio.state['busy'], '实时播放依赖了离线渲染'
            assert not studio.state['dirty'], '播放把已保存工程标为未保存'
            first = provider.requestImage('frame', QSize(), QSize())
            studio.setSetting('templateId', 'headline')
            second = provider.requestImage('frame', QSize(), QSize())
            assert first != second, '播放时切换模板未更新画面'
            assert studio.state['playbackPlaying'], '切模板中断了歌曲播放'
            studio.seek(2500); QTest.qWait(180)
            assert 2500 <= studio.state['positionMs'] < 2900, '跳转歌词与音频时钟不同步'
            assert studio.state['currentText'] == '第二句同步歌词'
            scrub = items['studioTimelineScrub']
            start = scrub.mapToScene(QPointF(scrub.width()*.4, 8))
            end = scrub.mapToScene(QPointF(scrub.width()*.6, 8))
            QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(start.x()), round(start.y())))
            QTest.mouseMove(window, QPoint(round(end.x()), round(end.y())), 50)
            QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(end.x()), round(end.y())))
            QTest.qWait(120)
            assert 2950 <= studio.state['positionMs'] < 3350, '实际拖动时间轴未同步音频'
            click(window, items['studioPreviewMute'])
            assert studio.state['playbackMuted'] and studio._audio_output.isMuted()
            click(window, transport); QTest.qWait(100)
            paused = studio.state['positionMs']; QTest.qWait(220)
            assert not studio.state['playbackPlaying'] and abs(studio.state['positionMs']-paused) < 60
            studio.seek(4700); click(window, transport); QTest.qWait(650)
            assert not studio.state['playbackPlaying'], '曲尾没有停止'
            assert studio.state['positionMs'] >= 4950, '曲尾时钟没有停在结束位置'
            click(window, transport); QTest.qWait(250)
            assert studio.state['playbackPlaying'] and studio.state['positionMs'] < 600, '曲尾不能重新播放'
            items['studioWorkspace'].setVisible(False); QTest.qWait(120)
            assert not studio.state['playbackPlaying'], '离开制作页仍在播放歌曲'
            assert not warnings, '\n'.join(warnings)
            print(json.dumps({'passed': True, 'checks': ['播放/暂停', '真实音频时钟', '播放时模板即时更新', '歌词与音频跳转同步', '拖动时间轴', '静音', '曲尾停止和重播', '离开页面暂停', '播放不改工程', '无需渲染'], 'warnings': warnings}, ensure_ascii=False))
        finally:
            studio.shutdown(); provider.shutdown(); window.close(); application.processEvents()


if __name__ == '__main__':
    main()
