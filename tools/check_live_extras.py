"""Real Qt check for guest messages, scan-to-send wishes, duet colours, show log,
MIDI learn, drag-in import with duplicate hints and the move pack.

Temporary runtime and synthetic WAV files only. The wish server binds to this
Mac's LAN address for the check and is stopped at the end. Screenshots go to
BAOFANG_UI_CHECK_DIR (default runtime/live-extras-20261029).
"""
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

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
from app.move_pack import export_pack
from tools.check_live_tools import make_audio, seed_library
from tools.ui_state_smoke import click, visual_items


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
    evidence = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root / 'runtime/live-extras-20261029'
    evidence.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='baofang-extras-', dir=evidence) as temp:
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
            QTest.qWait(100)

        try:
            window.resize(1000, 700)
            assert wait_for(lambda: not controller.state['stale']), 'replay did not start'

            # --- 对唱分色：演示盘歌词加上男/女标记（只在本检查的内存里） ---
            demo = controller.timelines['demo:A']
            tagged = dict(demo, segments=[dict(row, text=('男：' if i % 2 == 0 else '女：') + row['text'],
                                                words=[{'text': '男：' if i % 2 == 0 else '女：', 'startMs': row['startMs'], 'endMs': row['startMs'] + 1}] + row.get('words', []))
                                           for i, row in enumerate(demo['segments'])])
            controller.timelines['demo:A'] = tagged
            controller.seek(3)
            QTest.qWait(150)
            press('liveTab-style')
            press('liveDuetColors')
            assert controller.state['duetColors'] and controller.state['lyric']['singer'] == 'male', controller.state['lyric']
            assert '男' not in controller.state['lyric']['text'], 'tag still on screen'
            controller.seek(10)
            assert wait_for(lambda: controller.state['lyric'].get('singer') == 'female'), 'female line not coloured'
            results.append('对唱分色：男/女标记识别，标记不上屏')

            # --- 客屏插字 ---
            press('liveTab-message')
            field = find(window, 'screenTextInput')
            field.setProperty('text', '祝小王生日快乐')
            QTest.qWait(80)
            press('screenTextApply')
            assert controller.state['screenLayers']['text']['text'] == '祝小王生日快乐'
            assert controller.state['screenLayers']['text']['visible']
            banner = find(window, 'screenTextLayer')
            assert banner is not None and banner.isVisible(), 'banner not in preview'
            window.grabWindow().save(str(evidence / 'live-message-duet-1000x700.png'))
            press('screenTextToggle')
            assert not controller.state['screenLayers']['text']['visible']
            results.append('客屏插字：上屏、清除')

            # --- 扫码送祝福 ---
            press('screenWishesTab')
            QTest.qWait(200)
            press('wishServerToggle')
            qr = controller.state['guestQr']
            assert qr['running'] and qr['url'].startswith('http://'), qr
            request = urllib.request.Request(qr['url'], data=json.dumps({'text': '干杯！', 'name': '阿杰'}).encode(),
                                             headers={'Content-Type': 'application/json'}, method='POST')
            assert urllib.request.urlopen(request, timeout=3).status == 200
            assert wait_for(lambda: len(controller.state['guestInbox']) == 1), 'wish did not arrive'
            assert not controller.state['guestMessage']['visible'], 'wish must wait for approval'
            QTest.qWait(200)
            window.grabWindow().save(str(evidence / 'live-wish-1000x700.png'))
            wish = controller.state['guestInbox'][0]
            press('wishApprove-' + wish['id'])
            assert controller.state['guestMessage']['text'] == '阿杰：干杯！'
            press('wishServerToggle')
            assert not controller.state['guestQr']['running']
            press('liveTab-sync')
            QTest.qWait(200)
            results.append('扫码送祝福：手机发送 → 待审 → 上屏 → 关闭服务')

            # --- 演出记录 ---
            controller.clearOutput()
            controller.restoreOutput()
            controller.selectSource('B')
            controller.refreshShowLog('')
            kinds = [row['kind'] for row in controller.state['showLog']['rows']]
            for kind in ('message', 'hide', 'restore', 'source'):
                assert kind in kinds, (kind, kinds)
            results.append('演出记录：插字、隐藏、恢复、换盘都记下')

            # --- MIDI 学习：虚拟端口 ---
            try:
                import mido
                mido.set_backend('mido.backends.rtmidi')
                virtual = mido.open_output('Baofang Check', virtual=True)
            except Exception as exc:
                virtual = None
                results.append('MIDI：本机不能开虚拟端口，跳过实测（' + str(exc)[:60] + '）')
            if virtual:
                try:
                    controller.midiRefresh()
                    name = next((n for n in controller.state['midi']['ports'] if 'Baofang Check' in n), None)
                    assert name, controller.state['midi']['ports']
                    controller.midiSelectPort(name)
                    assert controller.state['midi']['connected'], controller.state['midi']
                    controller.selectSource('A')
                    controller.midiLearn('toggleLyrics')
                    virtual.send(mido.Message('note_on', note=40, velocity=90))
                    assert wait_for(lambda: controller.midi.mapping.get('toggleLyrics') == 'note:0:40'), 'learn failed'
                    virtual.send(mido.Message('note_on', note=40, velocity=90))
                    assert wait_for(lambda: controller.state['selectedSource'] == 'none'), 'midi press did not hide lyrics'
                    virtual.send(mido.Message('note_on', note=40, velocity=90))
                    assert wait_for(lambda: controller.state['selectedSource'] == 'A'), 'midi press did not restore'
                    results.append('MIDI：学习一个键，按键隐藏 / 恢复歌词')
                finally:
                    controller.midiSelectPort('')
                    virtual.close()

            # --- 拖入导入 + 可能重复 ---
            drop = Path(temp) / 'drop' / '子文件夹'
            make_audio(drop / '夜色开场.wav', 2000)
            (drop / '说明.txt').write_text('不是音频')
            controller.importDroppedPaths([QUrl.fromLocalFile(str(drop.parent)).toString()])
            assert wait_for(lambda: controller.state['offlineLibrary']['total'] == 4, 8000), controller.state['offlineLibrary']['total']
            assert controller.state['offlineLibrary']['duplicates'] == 1
            window.setProperty('currentPage', 1)
            QTest.qWait(300)
            window.grabWindow().save(str(evidence / 'library-duplicate-1000x700.png'))
            results.append('拖入文件夹：找到子文件夹音频、跳过文本，同名同长标「可能重复」')

            # --- 搬家包 ---
            controller.addToSetlist(tracks[0]['versionId'])
            controller.setLyricOffset(0)
            pack = export_pack(controller.library, folder, Path(temp) / 'pack')
            assert pack['lyrics'] == 1 and pack['settings'] >= 2 and pack['logs'] == 1, pack
            fresh_runtime = Path(temp) / 'new-mac'
            fresh = Controller(root, fresh_runtime)
            try:
                assert fresh.import_move_pack(pack['directory']), fresh.state['notice']
                assert fresh.setlist.items == [tracks[0]['versionId']]
                assert fresh.state['duetColors'] is True
                assert fresh.library.cache_directory.glob('*.json')
                assert fresh.showlog.days(), 'logs not moved'
            finally:
                fresh.shutdown()
            results.append('搬家包：歌词、设置、歌单、演出记录导出后在新目录导入')

            window.setProperty('currentPage', 0)
            window.resize(1440, 900)
            controller.setGuestMessage('欢迎来到 888 包房', 0)
            QTest.qWait(300)
            window.grabWindow().save(str(evidence / 'live-message-1440x900.png'))
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
