"""Real Qt check for tap sync, live single-key shortcuts, key help and tonight's set list.

Uses a temporary runtime with synthetic WAV files only. Screenshots at
1000x700 and 1440x900 go to BAOFANG_UI_CHECK_DIR (default runtime/live-tools-20261029).
"""
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import Q_ARG, QMetaObject, Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.controller import Controller
from app.storage import Library, inspect_audio
from tools.ui_state_smoke import click, visual_items


def make_audio(path, amplitude):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(struct.pack('<h', amplitude) * 32000)


def seed_library(folder):
    library = Library(folder)
    tracks = []
    for index, name in enumerate(('夜色开场', '副歌大合唱', '收尾慢歌')):
        audio = folder / 'audio' / (name + '.wav')
        make_audio(audio, 900 + index * 300)
        track = inspect_audio(audio)
        library.add_track(track)
        tracks.append(track)
    first = tracks[0]
    library.add_timeline({'schemaVersion': 1, 'versionId': first['versionId'], 'durationMs': 4000.,
                          'quality': 'sentence', 'provider': 'ui-check',
                          'segments': [{'startMs': 500., 'endMs': 1500., 'text': '开场第一句'},
                                       {'startMs': 2500., 'endMs': 3500., 'text': '开场第二句'}]}, first)
    library.close()
    return tracks


def find(window, name):
    return next((item for item in visual_items(window.contentItem()) if item.objectName() == name), None)


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root / 'runtime/live-tools-20261029'
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='baofang-tools-', dir=evidence) as temp:
        folder = Path(temp)
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
        results = []
        try:
            window.resize(1000, 700)
            QTest.qWait(400)
            assert not controller.state['stale'], 'replay did not start'

            def press(name):
                node = find(window, name)
                assert node is not None and node.isVisible() and node.isEnabled(), name + ' is not reachable'
                click(window, node)
                QTest.qWait(80)

            # --- 打拍对齐：演示 A 盘第一句在 2000ms 开唱 ---
            tap = find(window, 'liveTapSync')
            assert tap is not None and tap.isVisible(), 'tap button missing'
            controller.seek(2.25)
            QTest.qWait(60)
            window.requestActivate()
            QTest.keyClick(window, Qt.Key.Key_T)
            QTest.qWait(80)
            offset = controller.state['lyricOffsetMs']
            assert 150 <= offset <= 600, f'tap offset {offset} not near the 250ms late start'
            assert controller.state['lyricTap']['canUndo']
            results.append(f'T 键打拍：{offset} ms')
            QTest.keyClick(window, Qt.Key.Key_T, Qt.KeyboardModifier.ShiftModifier)
            QTest.qWait(80)
            assert controller.state['lyricOffsetMs'] == 0, 'Shift+T did not undo'
            results.append('Shift+T 撤销：0 ms')
            QTest.keyClick(window, Qt.Key.Key_BracketRight)
            QTest.qWait(60)
            assert controller.state['lyricOffsetMs'] == 100, '] did not delay'
            QTest.keyClick(window, Qt.Key.Key_BracketLeft)
            QTest.keyClick(window, Qt.Key.Key_BracketLeft)
            QTest.qWait(60)
            assert controller.state['lyricOffsetMs'] == -100, '[ did not advance'
            QTest.keyClick(window, Qt.Key.Key_Backslash)
            QTest.qWait(60)
            assert controller.state['lyricOffsetMs'] == 0, '\\ did not reset'
            results.append('[ ] \\ 微调与归零：通过')
            controller.seek(12)
            QTest.qWait(60)
            press('liveTapSync')
            assert controller.state['lyricOffsetMs'] == 0 and '附近没有句子开头' in controller.state['notice'], 'tap away from a line must not change sync'
            results.append('远离句首打拍：不改同步')

            # --- 快捷键表 ---
            QTest.keyClick(window, Qt.Key.Key_F1)
            QTest.qWait(150)
            help_popup = find(window, 'liveKeyHelpClose')
            assert help_popup is not None and help_popup.isVisible(), 'key help did not open'
            window.grabWindow().save(str(evidence / 'live-keyhelp-1000x700.png'))
            press('liveKeyHelpClose')
            QTest.qWait(200)
            results.append('F1 快捷键表：通过')

            # --- 歌单 ---
            card = find(window, 'liveSetlistNext')
            assert card is not None and card.isVisible() and '曲库' in card.property('text'), 'empty set-list hint missing'
            window.grabWindow().save(str(evidence / 'live-1000x700.png'))
            window.setProperty('currentPage', 1)
            QTest.qWait(300)
            listing = find(window, 'offlineTrackList')
            order = [row['versionId'] for row in controller.state['offlineLibrary']['tracks']]
            for track in tracks:
                QMetaObject.invokeMethod(listing, 'positionViewAtIndex', Q_ARG(int, order.index(track['versionId'])), Q_ARG(int, 4))
                QTest.qWait(120)
                press('offlineSetlist-' + track['versionId'])
            setlist = controller.state['setlist']
            assert setlist['total'] == 3 and setlist['counts']['ready'] == 1 and setlist['counts']['missing'] == 2, setlist
            assert not find(window, 'offlineSetlist-' + tracks[0]['versionId']).isEnabled(), 'listed track still addable'
            controller.moveSetlistItem(2, -1)
            QTest.qWait(80)
            assert [row['title'] for row in controller.state['setlist']['items']] == ['夜色开场', '收尾慢歌', '副歌大合唱']
            controller.setSetlistPlayed(0, True)
            QTest.qWait(80)
            assert controller.state['setlist']['next']['title'] == '收尾慢歌'
            window.grabWindow().save(str(evidence / 'library-setlist-1000x700.png'))
            window.resize(1440, 900)
            QTest.qWait(300)
            window.grabWindow().save(str(evidence / 'library-setlist-1440x900.png'))
            results.append('曲库加入、排序、标已放：通过')
            window.setProperty('currentPage', 0)
            QTest.qWait(250)
            line = find(window, 'liveSetlistNext').property('text')
            assert line == '下一首 · 收尾慢歌', line
            window.grabWindow().save(str(evidence / 'live-1440x900.png'))
            window.resize(1000, 700)
            QTest.qWait(250)
            results.append('现场页歌单卡：' + line)
            from app.setlist import Setlist
            assert Setlist(folder / 'setlist.json').items == [row['versionId'] for row in controller.state['setlist']['items']]
            results.append('歌单写入本机：通过')
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
    sys.exit(main())
