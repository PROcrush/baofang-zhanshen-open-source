"""Actual shared renderer pixels, transparency and sentence-timing regressions."""
import faulthandler
import hashlib
import json
import os
import traceback
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QObject, Property, QUrl, Signal
from PySide6.QtGui import QFont, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.lyric_paint import GradientLyricItem, register_lyric_types
from app.style_catalog import style_catalog
from app.styles import DEFAULT_STYLE, style_preset
from tools.ui_state_smoke import named_items


class Frame(QObject):
    stateChanged = Signal()

    def __init__(self):
        super().__init__()
        self.data = {'selectedSource': 'A', 'effectiveSource': 'A', 'stale': False,
                     'outputBlocked': False, 'inputMode': 'style-preview', 'scene': 'amber',
                     'deckA': {'cacheState': 'ready'}, 'style': dict(DEFAULT_STYLE),
                     'lyric': {'visible': True, 'text': '让旋律照亮此刻', 'nextText': '一起唱出心中的歌',
                               'quality': 'sentence', 'progress': .48}}

    @Property('QVariantMap', notify=stateChanged)
    def state(self):
        return self.data

    def publish(self):
        self.stateChanged.emit()
        QTest.qWait(70)


def rgba(image):
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return bytes(converted.bits())


def main():
    faulthandler.enable()
    faulthandler.dump_traceback_later(40, exit=True)
    root = Path(__file__).resolve().parents[1]
    evidence = root / 'runtime/style-presets-20261013/render'
    evidence.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    register_lyric_types()
    frame = Frame()
    engine = QQmlApplicationEngine()
    warnings, checks, failures, hashes, measurements = [], [], [], {}, {}
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    engine.rootContext().setContextProperty('controller', frame)
    engine.loadData(b'''import QtQuick
import "../ui"
Window { width: 1120; height: 630; visible: true; color: "transparent"
    ProgramView { anchors.fill: parent; stateData: controller ? controller.state : ({});
        transparentStage: true; presetThumbnail: true }
}''', QUrl.fromLocalFile(str(root / 'tools/preset-pixel-fixture.qml')))
    if not engine.rootObjects():
        return 2
    window = engine.rootObjects()[0]
    QTest.qWait(100)
    items = named_items(window)
    try:
        meta = GradientLyricItem.staticMetaObject
        for index in range(meta.propertyOffset(), meta.propertyCount()):
            prop = meta.property(index)
            assert prop.hasNotifySignal() and prop.notifySignal().isValid(), prop.name()
        checks.append('all painted-text meta-object properties have valid local notify signals')
        for entry in style_catalog():
            frame.data['style'] = style_preset(entry['id'])
            frame.publish()
            assert items['programView'].property('showLyrics')
            assert not items['programView'].property('backgroundVisible')
            assert not items['programView'].property('renderWordSweep')
            image = window.grabWindow()
            pixels = rgba(image)
            alpha = pixels[3::4]
            assert len(alpha) - alpha.count(0) > 100, entry['id'] + ' has no pixels'
            for x, y in ((8, 8), (1110, 8), (8, 620), (1110, 620)):
                assert image.pixelColor(x, y).alpha() == 0, entry['id'] + ' covers the video corner'
            hashes[entry['id']] = hashlib.sha256(pixels).hexdigest()
            assert image.save(str(evidence / (entry['id'] + '.png')))
            frame.data['style']['decorationEnabled'] = False
            frame.publish()
            plain = rgba(window.grabWindow())
            assert plain != pixels, entry['id'] + ' decoration is only a catalog label'
        assert len(set(hashes.values())) == 27
        checks.append('27 actual distinct rendered presets have visible local decoration and clear video corners')

        frame.data['style'] = dict(DEFAULT_STYLE, fontSize=80, baseColor='#00CCFF',
                                   gradientColor='#FF88CC', gradientEnabled=True,
                                   outlineWidth=0, shadowEnabled=False, verticalPosition=35,
                                   lineMode='single')
        frame.publish()
        pixels = rgba(window.grabWindow())
        red, alpha = pixels[0::4], pixels[3::4]
        first = next(i for i, value in enumerate(alpha) if value > 240)
        last = len(alpha) - 1 - next(i for i, value in enumerate(reversed(alpha)) if value > 240)
        top, bottom = first // 1120, last // 1120
        upper, lower = [], []
        for index in range(first, last + 1):
            if alpha[index] > 240:
                y = index // 1120
                if y <= top + (bottom - top) / 3:
                    upper.append(red[index])
                elif y >= bottom - (bottom - top) / 3:
                    lower.append(red[index])
        assert upper and lower
        upper_red, lower_red = sum(upper) / len(upper), sum(lower) / len(lower)
        assert lower_red - upper_red > 80, (upper_red, lower_red)
        measurements.update(gradientUpperRed=upper_red, gradientLowerRed=lower_red)
        assert window.grabWindow().save(str(evidence / 'gradient-glyphs.png'))
        checks.append('real glyph fill has measured cyan-to-pink gradient rather than a filled rectangle')

        frame.data['lyric']['text'] = '沿着节拍慢慢亮起' * 62
        frame.data['style'].update(fontSize=96, letterSpacing=8)
        frame.publish()
        label = items['currentLyricText']
        assert label.property('lineCount') > 1
        assert label.height() < window.height()
        assert not label.property('sweepEnabled')
        measurements['longLineCount'] = label.property('lineCount')
        checks.append('496-character gradient wraps, fits the output and keeps sentence timing')
        frame.data['stale'] = True
        frame.publish()
        alpha = rgba(window.grabWindow())[3::4]
        assert alpha.count(0) == len(alpha)
        checks.append('stale source hides every caption and decoration pixel')
        assert not warnings, '\n'.join(warnings)
    except Exception:
        failures.append(traceback.format_exc())
    finally:
        window.close()
        faulthandler.cancel_dump_traceback_later()
        report = {'passed': not failures, 'syntheticOnly': True, 'referenceRuntimeUsed': False,
                  'realSeratoValidated': False, 'checks': checks, 'measurements': measurements,
                  'presetPixelHashes': hashes, 'warnings': warnings, 'failures': failures}
        (evidence / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
