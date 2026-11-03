"""Capture the real main window across pages and sizes for before/after review.

Uses the product controllers on an isolated runtime (replay mode, no DJ app),
an in-memory Keychain stand-in, and the synthetic AX facade for original-mode
states. Never reads rekordbox, system permissions or a real API key.

    .venv/bin/python -m tools.capture_ui_pages OUT_DIR [RUNTIME_DIR]
"""
import os
import sys
from pathlib import Path

# Offscreen keeps exact 1000x700 / 1440x900 regardless of the display's menu bar.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPoint, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
SIZES = ((1000, 700), (1440, 900))


class MemoryKeychain:
    def read(self, host): return ''
    def write(self, host, key): raise ValueError('capture tool never stores keys')
    def delete(self, host): pass


def main():
    out = Path(sys.argv[1]).resolve()
    runtime = Path(sys.argv[2] if len(sys.argv) > 2 else out / 'runtime').resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setFont(QFont('PingFang SC', 13))
    from app.lyric_paint import register_lyric_types
    register_lyric_types()
    QQuickStyle.setStyle('Basic')
    from app.controller import Controller
    from app.studio import StudioController
    from app.studio_preview import StudioPreviewProvider
    from app.toolbox import ToolboxController
    from app.ai_settings import AISettings
    from tools.check_rekordbox_ax_ui import AxUiController

    warnings = []
    shots = []
    providers = []

    def run_engine(controller, studio, tools, ai, plan):
        provider = StudioPreviewProvider(studio)
        providers.append(provider)
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda ms: warnings.extend(m.toString() for m in ms))
        ctx = engine.rootContext()
        ctx.setContextProperty('controller', controller)
        ctx.setContextProperty('studio', studio)
        ctx.setContextProperty('studioTools', tools)
        ctx.setContextProperty('aiSettings', ai)
        engine.addImageProvider('studioPreview', provider)
        engine.load(QUrl.fromLocalFile(str(ROOT / 'ui' / 'Main.qml')))
        window = engine.rootObjects()[0]
        QTest.qWait(600)
        for name, page, prepare in plan:
            if prepare: prepare(window)
            for w, h in SIZES:
                window.setProperty('currentPage', page)
                window.resize(w, h)
                QTest.qWait(500)
                QTest.mouseMove(window, QPoint(w - 3, h - 3)); QTest.qWait(150)
                path = out / f'{name}-{w}x{h}.png'
                window.grabWindow().save(str(path))
                shots.append(path)
        window.close()
        engine.deleteLater()
        QTest.qWait(200)

    controller = Controller(ROOT, runtime / 'live')
    studio = StudioController(ROOT, runtime / 'studio')
    tools = ToolboxController(ROOT, runtime / 'tools')
    ai = AISettings(ROOT, runtime / 'api', controller, studio, credential_store=MemoryKeychain())
    QTest.qWait(1500)

    def open_example(window):
        studio.openExampleProject(); QTest.qWait(2500)

    run_engine(controller, studio, tools, ai, [
        ('studio-empty', 5, None),
        ('live-replay', 0, None),
        ('library', 1, None),
        ('prepare', 4, None),
        ('api', 7, None),
        ('studio-content', 5, open_example),
    ])

    ax = AxUiController()
    ax.setInputMode('rekordbox_ax')
    # 未就绪：两盘都未确认实际文件，来源不可用
    ax.data.update(effectiveSource='none', selectionReason='先确认当前盘实际歌曲，再指定 A / B 盘。')
    ax.emit_state()

    def bind_a(window):
        ax.bindRekordboxAX('A', ax.data['rekordboxAX']['candidates'][0]['versionId'])
        ax.data.update(effectiveSource='A'); ax.emit_state(); QTest.qWait(200)
    run_engine(ax, studio, tools, ai, [
        ('live-ax-unready', 0, None),
        ('live-ax-bound', 0, bind_a),
    ])

    controller.shutdown(); studio.shutdown(); tools.shutdown(); ai.shutdown()
    report = out / 'qml-warnings.txt'
    report.write_text('\n'.join(warnings) + '\n', encoding='utf-8')
    print(f'{len(shots)} screenshots in {out}; {len(warnings)} QML warnings -> {report}')
    for w in warnings[:40]: print('  ' + w)


if __name__ == '__main__':
    main()
