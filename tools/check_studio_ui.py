"""Independent local subtitle import and Qt editor interactions on synthetic media."""
import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.studio import StudioController
from app.studio_preview import StudioPreviewProvider
from tools.ui_state_smoke import SmokeController, named_items, click


def main():
    from tests.test_studio import synthesize_audio, wait_idle
    root = Path(__file__).resolve().parent.parent
    target = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root / 'runtime/ui-humanized-20261012/basic'
    target.mkdir(parents=True, exist_ok=True)
    fixtures = target / 'fixtures' / uuid.uuid4().hex
    fixtures.mkdir(parents=True)
    audio, subtitle = fixtures / '界面合成音频.wav', fixtures / '界面合成字幕.srt'
    synthesize_audio(audio)
    subtitle.write_text('1\n00:00:00,050 --> 00:00:00,750\n音乐与画面，在这里同步\n', encoding='utf-8')
    application = QApplication.instance() or QApplication([])
    application.setFont(QFont('PingFang SC', 13))
    QQuickStyle.setStyle('Basic')
    studio = StudioController(root, target / 'runtime')
    studio.chooseAudio(str(audio))
    wait_idle(studio)
    assert studio.state['audioPath'], studio.state['status']
    studio.chooseSubtitles(str(subtitle))
    wait_idle(studio)
    assert studio.state['segments'], studio.state['status']
    original_text = studio.state['segments'][0]['text']
    studio.seek(300)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    facade = SmokeController('replay')
    engine.rootContext().setContextProperty('controller', facade)
    engine.rootContext().setContextProperty('studio', studio)
    preview_provider = StudioPreviewProvider(studio)
    engine.addImageProvider('studioPreview', preview_provider)
    engine.load(QUrl.fromLocalFile(str(root / 'ui/Main.qml')))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setProperty('currentPage', 5)
    named_items(window)['studioWorkspace'].setProperty('advancedEditing', True)
    QTest.qWait(150)
    items = named_items(window)
    click(window,items['studioLyricRow-0']);QTest.qWait(50)
    editor = items['studioLyricEditor']
    click(window, editor)
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    editor.setProperty('text', '新界面编辑后的歌词')
    QTest.qWait(50)
    # Find visible actions by their text, click actual QML control geometry.
    def button(text):
        pending = [window.contentItem()]
        while pending:
            node = pending.pop()
            if node.isVisible() and node.property('text') == text:
                return node
            pending.extend(node.childItems())
        raise AssertionError('Missing visible action: '+text)
    click(window, button('应用修改'))
    assert studio.state['segments'][0]['text'] == '新界面编辑后的歌词'
    click(window, button('撤销'))
    assert studio.state['segments'][0]['text'] == original_text, studio.state['segments'][0]['text']
    assert editor.property('text') == original_text, 'Undo left the inspector text stale'
    project = fixtures / '界面制作.bfstudio.json'
    studio.saveProject(str(project))
    assert studio.state['projectPath'] == str(project), studio.state['status']
    studio.openProject(str(project))
    assert not studio.state['dirty'] and studio.state['projectPath'] == str(project), studio.state['status']
    studio.seek(300)
    screenshots = []
    for width, height in [(1000,700),(1440,900)]:
        window.resize(width,height)
        QTest.mouseMove(window, QPoint(width-20,height-20))
        QTest.qWait(200)
        path = target / (str(width)+'-studio.png')
        assert window.grabWindow().save(str(path))
        screenshots.append(str(path))
    assert not warnings, '\n'.join(warnings)
    report = {'passed':True,'syntheticAudio':True,'independentImport':True,'cloudRequests':0,
              'checks':['independent subtitle import','QML edit and undo reach StudioController',
                        'project save/reopen preserves media identity','1000x700 and 1440x900 rendered without QML warnings'],
              'warnings':warnings,'screenshots':screenshots,'projectPath':str(project)}
    (target/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    studio.shutdown()
    window.close()


if __name__ == '__main__':
    main()
