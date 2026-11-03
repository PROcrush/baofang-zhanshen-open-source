import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from app.controller import Controller, FileController
from app.runtime_paths import default_runtime


def main():
    if sys.platform == 'darwin':
        from scripts.repair_qt_metadata import repair_qt_metadata
        repair_qt_metadata()
    parser = argparse.ArgumentParser(description='包房战神 Qt 开发版')
    parser.add_argument('--renderer', action='store_true')
    parser.add_argument('--serato-overlay', action='store_true', help='透明字幕叠加到已核验的 Serato 视频输出窗口')
    parser.add_argument('--output', action='store_true')
    parser.add_argument('--serato', action='store_true', help='以 Serato Video 只读联动启动')
    parser.add_argument('--rekordbox-ax', action='store_true', help='通过辅助功能只读跟随原版 rekordbox')
    parser.add_argument('--rekordbox', action='store_true', help='跟随已准备的 rekordbox 7.2.19 测试副本')
    parser.add_argument('--studio', action='store_true', help='打开包房战神制作工作台')
    parser.add_argument('--live', action='store_true', help='打开现场工作台')
    parser.add_argument('--api-settings', action='store_true', help='打开国内语音识别与文字纠错模型配置')
    parser.add_argument('--studio-project', default=None, help='打开本机包房战神制作工程')
    parser.add_argument('--runtime', default=None)
    parser.add_argument('--smoke-seconds', type=float, default=0)
    parser.add_argument('--screenshot', default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    runtime = Path(args.runtime).resolve() if args.runtime else default_runtime(root)
    application = QApplication(sys.argv)
    application.setApplicationName('包房战神')
    application.setOrganizationName('BaofangZhanshen')
    application.setFont(QFont('PingFang SC', 13))
    from app.lyric_paint import register_lyric_types
    register_lyric_types()
    QQuickStyle.setStyle('Basic')
    engine = QQmlApplicationEngine()
    input_mode = ('rekordbox_ax' if args.rekordbox_ax else 'rekordbox' if args.rekordbox
                  else 'serato' if args.serato else None)
    controller = FileController(root, runtime) if args.renderer else Controller(root, runtime, input_mode=input_mode)
    engine.rootContext().setContextProperty('controller', controller)
    studio_controller = None
    toolbox_controller = None
    ai_settings = None
    if not args.renderer:
        from app.studio import StudioController
        studio_controller = StudioController(root, runtime)
        engine.rootContext().setContextProperty('studio', studio_controller)
        from app.studio_preview import StudioPreviewProvider
        studio_preview_provider = StudioPreviewProvider(studio_controller)
        engine.addImageProvider('studioPreview', studio_preview_provider)
        from app.toolbox import ToolboxController
        toolbox_controller = ToolboxController(root, runtime)
        engine.rootContext().setContextProperty('studioTools', toolbox_controller)
        from app.ai_settings import AISettings
        ai_settings=AISettings(root,runtime,controller,studio_controller)
        engine.rootContext().setContextProperty('aiSettings',ai_settings)
    path = root / 'ui' / (('SeratoOverlay.qml' if args.serato_overlay else 'Program.qml') if args.renderer else 'Main.qml')
    engine.load(QUrl.fromLocalFile(str(path)))
    if not engine.rootObjects():
        if not args.renderer:
            controller.shutdown()
            studio_controller.shutdown()
            toolbox_controller.shutdown()
            ai_settings.shutdown()
        return 2
    if not args.renderer:
        application.aboutToQuit.connect(controller.shutdown)
        application.aboutToQuit.connect(studio_controller.shutdown)
        application.aboutToQuit.connect(studio_preview_provider.shutdown)
        application.aboutToQuit.connect(toolbox_controller.shutdown)
        application.aboutToQuit.connect(ai_settings.shutdown)
        if args.studio or args.studio_project:
            engine.rootObjects()[0].setProperty('currentPage', 5)
        if args.studio_project:
            studio_controller.openProject(args.studio_project)
        if args.live:
            engine.rootObjects()[0].setProperty('currentPage', 0)
        if args.api_settings:
            engine.rootObjects()[0].setProperty('currentPage', 7)
        if args.output and (args.live or not (args.studio or args.studio_project)):
            QTimer.singleShot(500, controller.openOutput)
    elif args.serato_overlay:
        from app.serato_overlay import SeratoOverlay
        try:
            serato_overlay = SeratoOverlay(root, runtime, controller, engine.rootObjects()[0])
        except (OSError, RuntimeError) as exc:
            print('Serato 字幕叠加启动失败：' + str(exc), file=sys.stderr)
            return 2
        application.aboutToQuit.connect(serato_overlay.shutdown)
    else:
        controller.attach_output_window(engine.rootObjects()[0])
    if args.screenshot:
        def capture():
            window = engine.rootObjects()[0]
            window.grabWindow().save(str(Path(args.screenshot).resolve()))
        QTimer.singleShot(1500, capture)
    if args.smoke_seconds:
        QTimer.singleShot(int(args.smoke_seconds * 1000), application.quit)
    return application.exec()


if __name__ == '__main__':
    sys.exit(main())
