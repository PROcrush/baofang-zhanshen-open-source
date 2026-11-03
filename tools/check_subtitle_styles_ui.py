"""Real local style controls, persisted preferences and both QML output paths.

This uses an isolated original replay fixture. It never reads Serato, uploads
media, touches source audio, or claims external TV / native host verification.
"""
import json
import os
import tempfile
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
from app.styles import DEFAULT_STYLE
from tools.ui_state_smoke import click, named_items


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root / 'runtime/subtitle-style-upgrade-20261012'
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    warnings, checks, failures, screenshots = [], [], [], []
    controller = output = None
    windows, engines = [], []
    measurements = {}
    with tempfile.TemporaryDirectory(prefix='style-ui-', dir=evidence) as folder:
        runtime = Path(folder)
        try:
            # An old installation has no new fields; preserve the operator's settings.
            legacy = {key: DEFAULT_STYLE[key] for key in ('fontFamily', 'fontSize', 'letterSpacing',
                      'brightness', 'sweepMode', 'outputMode', 'dynamicBackground', 'countdownEnabled')}
            legacy['fontSize'] = 58
            (runtime / 'settings.json').write_text(json.dumps({'schemaVersion': 1, 'scene': 'amber',
                                                     'style': legacy, 'lyricOffsets': {},
                                                     'outputTarget': {'screenName': '', 'fullScreen': False}}))
            controller = Controller(root, runtime)
            output = FileController(root, runtime)
            assert controller.state['style']['fontSize'] == 58
            assert controller.state['style']['verticalPosition'] == DEFAULT_STYLE['verticalPosition']
            checks.append('legacy saved font size survives validated preference migration')
            for name, model in (('Main.qml', controller), ('Program.qml', output)):
                engine = QQmlApplicationEngine()
                engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
                engine.rootContext().setContextProperty('controller', model)
                engine.load(QUrl.fromLocalFile(str(root / 'ui' / name)))
                assert engine.rootObjects(), name + ' failed to load'
                engines.append(engine)
                windows.append(engine.rootObjects()[0])
            window, guest = windows
            QTest.qWait(350)
            controller.seek(3)
            QTest.qWait(120)
            window.setProperty('currentPage', 3)
            QTest.qWait(100)
            items, out = named_items(window), named_items(guest)
            click(window, items['style-adjustTab'])
            assert out['programView'].property('showLyrics'), 'replay fixture has no live lyric'
            scroll = items['style-settingsScroll']
            flick = scroll.property('contentItem')

            def reach(name):
                item = items[name]
                ancestor = item.parentItem()
                in_scroll = False
                while ancestor is not None:
                    if ancestor == flick:
                        in_scroll = True
                        break
                    ancestor = ancestor.parentItem()
                if in_scroll:
                    flick.setProperty('contentY', 0)
                    QTest.qWait(30)
                    target = item.mapToItem(flick, QPointF(0, 0)).y()
                    flick.setProperty('contentY', max(0, min(target - 12, flick.property('contentHeight') - flick.height())))
                    QTest.qWait(50)
                top = item.mapToScene(QPointF(0, 0))
                assert item.isVisible() and item.isEnabled(), name + ' unavailable'
                assert top.y() >= 0 and top.y() + item.height() <= window.height(), name + ' not in visible viewport'
                return item

            def press(name):
                click(window, reach(name))

            def slider(name):
                control = reach(name)
                click(window, control)
                previous = controller.state['style'][name.removeprefix('style-').removesuffix('Slider')]
                QTest.keyClick(window, Qt.Key.Key_Right)
                QTest.qWait(100)
                return previous

            for size in ((1000, 700), (1440, 900)):
                window.resize(*size)
                QTest.qWait(100)
                preview = items['style-livePreview']
                point = preview.mapToScene(QPointF(0, 0))
                assert preview.isVisible() and preview.width() > 200 and preview.height() > 100
                assert point.x() >= 0 and point.y() >= 0 and point.x() + preview.width() <= size[0] and point.y() + preview.height() <= size[1]
                press('style-preset-neon')
                assert controller.state['style']['highlightColor'] == '#6AE4FF'
                assert output.state['style']['highlightColor'] == '#6AE4FF'
                assert out['programView'].property('renderWordSweep'), 'valid word fixture did not show sweep'
                path = evidence / f'style-{size[0]}.png'
                assert window.grabWindow().save(str(path))
                screenshots.append(str(path))
            checks.append('1000x700 and 1440x900 keep live preview and one-click presets visible')
            press('style-single')
            assert not out['nextLyricText'].isVisible()
            press('style-standard')
            assert out['nextLyricText'].isVisible()
            press('style-karaoke')
            assert controller.state['style']['lineMode'] == 'karaoke' and out['traditionalKaraokeLayer'].isVisible()
            assert out['programView'].property('renderWordSweep')
            press('style-sentence')
            assert not out['programView'].property('renderWordSweep')
            checks.append('single, double and karaoke controls change independent QML output')

            # The following detailed geometry/color controls exercise the legacy double renderer.
            press('style-standard')

            slider('style-fontSizeSlider')
            assert out['currentLyricText'].property('font').pixelSize() == controller.state['style']['fontSize']
            slider('style-letterSpacingSlider')
            assert abs(out['currentLyricText'].property('font').letterSpacing() - controller.state['style']['letterSpacing']) < .01
            press('style-fontBoldToggle')
            assert not controller.state['style']['fontBold']
            press('style-baseColor-#FF9CCB')
            assert out['currentLyricText'].property('textColor').name().upper() == '#FF9CCB'
            image = guest.grabWindow()
            label = out['currentLyricText']
            corner = label.mapToScene(QPointF(0, 0))
            changed_pixels = 0
            for y in range(max(0, int(corner.y())), min(image.height(), int(corner.y() + label.height()))):
                for x in range(max(0, int(corner.x())), min(image.width(), int(corner.x() + label.width()))):
                    pixel = image.pixelColor(x, y)
                    if pixel.red() > 220 and 100 < pixel.green() < 190 and pixel.blue() > 170:
                        changed_pixels += 1
            assert changed_pixels > 50, 'edited text color never reached rendered output pixels'
            measurements['editedColorPixels'] = changed_pixels
            path = evidence / 'edited-color-output.png'
            assert image.save(str(path))
            screenshots.append(str(path))
            press('style-highlightColor-#A6EDB4')
            assert out['programView'].property('accent').name().upper() == '#A6EDB4'
            slider('style-outlineWidthSlider')
            assert abs(out['currentLyricText'].property('outlineWidth') - controller.state['style']['outlineWidth']) < .01
            press('style-shadowToggle')
            assert out['currentLyricText'].property('shadowEnabled') == controller.state['style']['shadowEnabled']
            before = out['lyricLayer'].y()
            slider('style-verticalPositionSlider')
            after = out['lyricLayer'].y()
            assert before != after, 'vertical position control did not move output text'
            press('style-align-right')
            assert out['programView'].property('lyricAlignment') == 2
            checks.append('actual clicks and keyboard edit font, spacing, color, outline, shadow, position and alignment')
            measurements.update(positionBefore=before, positionAfter=after,
                                outputFont=out['currentLyricText'].property('font').pixelSize())

            # Same renderer at a different host size scales the chosen setting.
            guest.resize(1680, 1080)
            QTest.qWait(100)
            assert abs(out['currentLyricText'].property('font').pixelSize() - controller.state['style']['fontSize'] * 1.5) <= 1
            checks.append('host/output resize scales selected font size from 1120 reference width')
            guest.resize(1120, 720)
            QTest.qWait(100)

            # Presets retain the switch; estimates keep their sentence quality.
            timeline = controller.timelines['demo:A']
            controller.timelines['demo:A'] = dict(timeline, quality='sentence', segments=[
                {k: v for k, v in row.items() if k != 'words'} for row in timeline['segments']])
            controller.setStylePreset('classic')
            QTest.qWait(100)
            assert output.state['lyric']['quality'] == 'sentence'
            assert out['programView'].property('showLyrics')
            assert not out['programView'].property('renderWordSweep')
            assert '走带已关闭' in items['style-syncQualityNote'].property('text')
            press('style-word')
            assert out['programView'].property('renderWordSweep')
            assert output.state['lyric']['quality'] == 'sentence'
            assert output.state['lyric']['sweepTiming'] == 'sentence'
            assert '按句估算' in items['style-syncQualityNote'].property('text')
            checks.append('sentence fixture preserves switch and estimates sweep without invented word timing')

            # The native overlay uses this exact renderer, with a transparent stage.
            overlay_engine = QQmlApplicationEngine()
            overlay_engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
            overlay_engine.rootContext().setContextProperty('controller', output)
            overlay_engine.load(QUrl.fromLocalFile(str(root / 'ui/SeratoOverlay.qml')))
            assert overlay_engine.rootObjects(), 'SeratoOverlay.qml failed to load'
            engines.append(overlay_engine)
            overlay = overlay_engine.rootObjects()[0]
            windows.append(overlay)
            overlay.show()
            overlay_items = named_items(overlay)
            view = overlay_items['programView']
            assert view.property('transparentStage')
            controller.setStyle('dynamicBackground', True)
            QTest.qWait(100)
            assert not view.property('backgroundVisible')
            assert view.property('color').alpha() == 0
            assert not overlay_items['lyricModeNote'].isVisible()
            assert view.property('showLyrics')
            assert overlay.grabWindow().save(str(evidence / 'transparent-lyric-layer.png'))
            image = overlay.grabWindow()
            assert image.pixelColor(8, 8).alpha() == 0, 'transparent layer still paints an opaque background'
            screenshots.append(str(evidence / 'transparent-lyric-layer.png'))
            checks.append('transparent shared renderer keeps host video pixels clear and shows the chosen style')
            saved = json.loads(controller.settings_path.read_text())['style']
            assert saved == output.state['style']
            controller.shutdown()
            controller = None
            reloaded = Controller(root, runtime)
            try:
                assert reloaded.state['style'] == saved
                checks.append('edited settings survive controller restart and retain all added fields')
            finally:
                reloaded.shutdown()
            assert not warnings, '\n'.join(warnings)
        except Exception as exc:
            import traceback
            failures.append(traceback.format_exc())
        finally:
            if controller:
                controller.shutdown()
            if output:
                output.timer.stop()
            for window in windows:
                window.close()
            report = {'passed': not failures, 'syntheticOnly': True, 'realDJValidated': False,
                      'nativeHostAlignmentValidated': False, 'externalTVValidated': False,
                      'checks': checks, 'measurements': measurements, 'warnings': warnings,
                      'failures': failures, 'screenshots': screenshots}
            (evidence / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
