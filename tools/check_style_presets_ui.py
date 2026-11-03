"""Exercise the local preset library with real QML, isolated state and output.

No Serato, reference app, source media, network or paid recognition is used.
"""
import json
import faulthandler
import os
import tempfile
import traceback
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
from app.controller import Controller, FileController
from app.lyric_paint import register_lyric_types
from app.style_catalog import style_catalog
from tools.ui_state_smoke import click, named_items


def main():
    faulthandler.enable()
    faulthandler.dump_traceback_later(40, exit=True)
    root = Path(__file__).resolve().parents[1]
    evidence = root / 'runtime/style-presets-20261013/ui'
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    print('GUI application ready', flush=True)
    application.setFont(QFont('PingFang SC', 13))
    register_lyric_types()
    QQuickStyle.setStyle('Basic')
    warnings, checks, failures, images = [], [], [], []
    controller = output = None
    windows, engines = [], []
    applied = 0
    with tempfile.TemporaryDirectory(prefix='isolated-', dir=evidence) as folder:
        try:
            runtime = Path(folder)
            controller = Controller(root, runtime)
            output = FileController(root, runtime)
            print('Isolated controllers ready', flush=True)
            for filename, model in (('Main.qml', controller), ('Program.qml', output)):
                engine = QQmlApplicationEngine()
                engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
                engine.rootContext().setContextProperty('controller', model)
                print('Loading ' + filename, flush=True)
                engine.load(QUrl.fromLocalFile(str(root / 'ui' / filename)))
                assert engine.rootObjects(), filename
                engines.append(engine)
                windows.append(engine.rootObjects()[0])
                print('Loaded ' + filename, flush=True)
            window, guest = windows
            QTest.qWait(400)
            controller.seek(3)
            window.setProperty('currentPage', 3)
            QTest.qWait(150)
            items = named_items(window)
            out = named_items(guest)
            library = items['style-presetLibrary']
            grid = items['style-presetGrid']
            search = items['style-presetSearch']
            catalog = style_catalog()
            assert len(catalog) == 27 and sum(len(row['schemes']) for row in catalog) == 88
            assert grid.property('count') == 27
            print('Preset library ready: 27 / 88', flush=True)

            def press(name):
                item = named_items(window)[name]
                point = item.mapToScene(QPointF(0, 0))
                assert item.isVisible() and item.isEnabled(), name
                assert point.y() >= 0 and point.y() + item.height() <= window.height(), name + ' outside viewport'
                click(window, item)
                QTest.qWait(70)

            # Keyboard input exercises the actual search control rather than only its model.
            window.requestActivate()
            click(window, search)
            search.forceActiveFocus()
            for key in (Qt.Key.Key_H, Qt.Key.Key_U, Qt.Key.Key_D):
                QTest.keyClick(window, key)
            QTest.qWait(80)
            assert grid.property('count') == 1, (search.property('text'), grid.property('count'))
            press('style-card-jizura-hud')
            assert controller.state['style']['presetId'] == 'jizura-hud'
            assert output.state['style']['decorationMode'] == 'hud'
            checks.append('keyboard search and card click apply HUD to live shared output')

            # Reach each style through the UI, then click every palette actually shown.
            for row in catalog:
                print('Applying ' + row['id'], flush=True)
                search.setProperty('text', row['name'])
                QTest.qWait(50)
                assert grid.property('count') >= 1
                press('style-card-' + row['id'])
                for index in range(len(row['schemes'])):
                    press('style-scheme-' + str(index))
                    selected = controller.state['style']
                    assert selected['presetId'] == row['id'] and selected['schemeIndex'] == index
                    assert output.state['style'] == selected
                    assert out['currentLyricText'].property('textColor').name().upper() == selected['baseColor'].upper()
                    assert out['programView'].property('showLyrics')
                    applied += 1
            checks.append('all 27 cards and all 88 palette buttons reach persisted controller and output')

            # This song is a sentence SRT; choosing karaoke-looking artwork must not invent timing.
            original = controller.timelines['demo:A']
            controller.timelines['demo:A'] = dict(original, quality='sentence', segments=[
                {key: value for key, value in row.items() if key != 'words'} for row in original['segments']])
            search.setProperty('text', '金色之夜')
            QTest.qWait(60)
            press('style-card-jizura-gold')
            assert output.state['lyric'].get('sweepTiming')=='sentence'
            assert output.state['lyric']['quality'] == 'sentence'
            checks.append('new preset keeps sentence subtitles free of invented word timing')

            for size in ((1000, 700), (1440, 900)):
                window.resize(*size)
                search.setProperty('text', '')
                QTest.qWait(100)
                for name in ('style-livePreview', 'style-presetSearch', 'style-libraryTab', 'style-adjustTab', 'style-activePresetName'):
                    item = named_items(window)[name]
                    point = item.mapToScene(QPointF(0, 0))
                    assert item.isVisible() and item.width() > 20
                    assert point.x() >= 0 and point.y() >= 0
                    assert point.x() + item.width() <= size[0] and point.y() + item.height() <= size[1]
                path = evidence / ('library-' + str(size[0]) + '.png')
                assert window.grabWindow().save(str(path))
                images.append(str(path))
                assert grid.height() > 140 and grid.width() > 450, (size, grid.width(), grid.height())
            checks.append('library, palette and persistent preview fit 1000x700 and 1440x900')

            press('style-adjustTab')
            scroll = named_items(window)['style-settingsScroll']
            flick = scroll.property('contentItem')
            toggle = named_items(window)['style-decorationToggle']
            target = toggle.mapToItem(flick, QPointF(0, 0)).y()
            flick.setProperty('contentY', max(0, min(target - 30, flick.property('contentHeight') - flick.height())))
            QTest.qWait(100)
            press('style-decorationToggle')
            assert controller.state['style']['decorationEnabled'] is False
            assert controller.state['style']['presetCustomized']
            assert controller.state['style']['presetId'] == 'jizura-gold'
            press('style-libraryTab')
            assert '已微调' in named_items(window)['style-activePresetName'].property('text')
            press('style-scheme-1')
            assert controller.state['style']['decorationEnabled']
            assert not controller.state['style']['presetCustomized']
            checks.append('manual decoration edit is marked and a palette restores the complete preset')

            saved = json.loads(controller.settings_path.read_text())['style']
            controller.shutdown()
            controller = None
            reloaded = Controller(root, runtime)
            try:
                assert reloaded.state['style'] == saved
                checks.append('selected card, palette, gradients and decoration persist across restart')
            finally:
                reloaded.shutdown()
            assert not warnings, '\n'.join(warnings)
        except Exception:
            failures.append(traceback.format_exc())
        finally:
            if controller:
                controller.shutdown()
            if output:
                output.timer.stop()
            for window in windows:
                window.close()
            report = {'passed': not failures, 'syntheticOnly': True, 'referenceRuntimeUsed': False,
                      'realSeratoPresetAppearanceValidated': False, 'appliedPalettes': applied,
                      'checks': checks, 'warnings': warnings, 'failures': failures, 'screenshots': images}
            (evidence / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False, indent=2))
    faulthandler.cancel_dump_traceback_later()
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
