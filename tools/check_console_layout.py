"""Synthetic visual geometry checks; no DJ connection, credentials, or cloud."""
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from tools.ui_state_smoke import SmokeController, named_items, click, synthetic_state


def main():
    root = Path(__file__).resolve().parent.parent
    target = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root / 'runtime/ui-user-20261014/layout'
    target.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    model = SmokeController('replay')
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    engine.rootContext().setContextProperty('controller', model)
    engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
    assert engine.rootObjects(), 'Main.qml did not load'
    window = engine.rootObjects()[0]
    checks, screenshots = [], []

    def bounds(item):
        point = item.mapToScene(QPointF(0, 0))
        return point.x(), point.y(), point.x() + item.width(), point.y() + item.height()

    for width, height in [(1000, 700), (1280, 820), (1440, 900), (1920, 1080)]:
        window.resize(width, height)
        window.setProperty('currentPage', 0)
        QTest.qWait(100)
        items = named_items(window)
        preview, rail, source = (items[name] for name in ('programPreviewFrame', 'monitorRail', 'sourceControlBar'))
        p, r, s = bounds(preview), bounds(rail), bounds(source)
        assert preview.width() >= 320, 'Guest preview collapsed'
        # 歌暮式三栏：左打碟机 / 中预览 / 右歌词控制
        assert abs(rail.width() - (230 if width < 1180 else 270)) < 1, 'Deck rail lost its bounded width'
        assert r[2] + 8 <= p[0], 'Deck monitor overlaps preview'
        assert p[2] + 8 <= s[0], 'Preview overlaps lyric controls'
        assert s[2] <= width + 1 and max(p[3], r[3], s[3]) <= height + 1, 'Live columns extend beyond window'
        for deck in ('A', 'B'):
            card = items['deckCard-' + deck]
            content = items['deckContents-' + deck]
            assert content.implicitHeight() <= content.height() + 1, f'Deck {deck} status clipped: required {content.implicitHeight()}, available {content.height()}, card {card.height()}'
        # 歌暮式右栏：来源与隐藏常驻，同步 / 样式 / 插字在标签页里
        for tab, names in (('liveTab-sync', ('liveTapSync', 'liveOffsetLater', 'source-auto', 'source-A', 'source-B', 'clearOutputButton', 'playbackButton', 'liveOverlayButton', 'liveOutputButton', 'liveOutputStatus', 'liveUpcomingLyric')),
                           ('liveTab-style', ('liveQuickKaraoke', 'liveLyricSweep', 'liveStyleSettings', 'liveDuetColors', 'clearOutputButton')),
                           ('liveTab-message', ('screenTextInput', 'screenTextApply', 'screenExtrasHide', 'clearOutputButton'))):
            click(window, items[tab])
            QTest.qWait(60)
            for name in names:
                assert items[name].isVisible(), name + ' hidden under ' + tab
                b = bounds(items[name])
                assert 0 <= b[0] < b[2] <= width and 0 <= b[1] < b[3] <= height, name + ' is outside the viewport'
        click(window, items['liveTab-sync'])
        QTest.qWait(30)
        for page, control in [(0, 'nav-0'), (5, 'nav-5'), (1, 'nav-1'), (4, 'nav-4'), (3, 'nav-3'), (2, 'nav-2')]:
            if control not in items or not items[control].isVisible():
                click(window, items['appSettingsMenu'])
                QTest.qWait(120)
                items.update(named_items(window))
            click(window, items[control])
            QTest.qWait(30)
            assert window.property('currentPage') == page, 'Navigation missed ' + control
            if page == 5:
                studio_preview = bounds(items['studioPreviewFrame'])
                studio_inspector = bounds(items['studioInspector'])
                studio_timeline = bounds(items['studioTimeline'])
                if items['studioInspector'].isVisible():
                    assert studio_preview[2] <= studio_inspector[0] + 1, 'Studio preview overlaps inspector'
                else:
                    assert studio_preview[0]>=0 and studio_preview[2]<=width+1, 'Empty studio preview extends beyond window'
                if items['studioTimeline'].isVisible():
                    assert max(studio_preview[3], studio_inspector[3]) + 8 <= studio_timeline[1], 'Studio timeline overlaps upper workspace'
                else:
                    assert not items['studioLibrary'].isVisible(), 'Empty studio shows editing tools before a song is selected'
            path = target / (str(width) + '-' + str(page) + '.png')
            assert window.grabWindow().save(str(path))
            screenshots.append(str(path))
        window.setProperty('currentPage', 0)
        QTest.keyClick(window, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(50)
        assert window.property('currentPage') == 4, 'Keyboard preparation shortcut failed'
        checks.append({'viewport': [width, height], 'previewWidth': round(preview.width(), 2),
                       'railWidth': rail.width(), 'navigation': 'six real clicks and keyboard shortcut passed',
                       'overlap': False, 'deckClipping': False})
    # Lyrics being ready must not imply that a guest output is open.
    window.resize(1000, 700)
    window.setProperty('currentPage', 0)
    model.data = synthetic_state('serato')
    model.data.update(outputOpen=False)
    model.emit_state()
    QTest.qWait(80)
    items = named_items(window)
    assert items['liveOutputStatus'].property('text') == '输出未开启'
    assert '正在跟随' in items['liveReadinessTitle'].property('text')
    screenshots.append(str(target / '1000-output-closed.png'))
    assert window.grabWindow().save(screenshots[-1])
    for name in ('liveInputModeCombo', 'connectSeratoButton', 'bridgeStopButton', 'liveDiagnosticsButton', 'liveScreenSettings', 'liveOverlayButton', 'liveOutputButton'):
        b = bounds(items[name])
        assert 0 <= b[0] < b[2] <= 1000 and 0 <= b[1] < b[3] <= 700, name+' clipped in Serato mode'
    model.data.update(outputOpen=True, outputDestination='serato_overlay', overlayStatus={'phase':'waiting','detail':'请打开 Serato Video Output'})
    model.emit_state()
    QTest.qWait(80)
    assert items['liveOutputStatus'].property('tone') == 'amber'
    assert items['liveOutputStatus'].property('text') == '等待 Serato 视频窗口'
    model.data['overlayStatus'] = {'phase':'error','detail':'合成错误：无法访问视频窗口'}
    model.emit_state()
    QTest.qWait(80)
    assert items['liveOutputStatus'].property('text') == '字幕叠加失败'
    assert items['liveOutputStatus'].property('tone') == 'red'
    model.data['overlayStatus'] = {'phase':'attached','detail':'字幕已叠加'}
    model.emit_state()
    QTest.qWait(80)
    assert items['liveOutputStatus'].property('text') == 'Serato 字幕已叠加'
    model.data.update(selectedSource='none', effectiveSource='none')
    model.emit_state()
    QTest.qWait(80)
    assert items['liveUpcomingLyric'].property('text') != model.data['lyric']['nextText'], 'Hidden lyrics leak into the next-line monitor'
    model.data.update(selectedSource='auto', effectiveSource='A', stale=True)
    model.emit_state()
    QTest.qWait(80)
    assert items['liveUpcomingLyric'].property('text') != model.data['lyric']['nextText'], 'Stale lyrics remain in the next-line monitor'
    checks.append({'outputStates':'closed, waiting, failed and attached remain distinct from lyric readiness', 'hiddenAndStale':'upcoming lyric monitor clears immediately'})
    assert not warnings, '\n'.join(warnings)
    report = {'passed': True, 'syntheticOnly': True, 'realDJValidated': False,
              'checks': checks, 'warnings': warnings, 'screenshots': screenshots}
    (target / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key != 'screenshots'}, ensure_ascii=False, indent=2))
    window.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
