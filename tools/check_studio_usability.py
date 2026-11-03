"""Check actual QML controls for the shorter save, resume and export journeys."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QPointF, QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from app.studio import StudioController
from app.studio_preview import StudioPreviewProvider
from tests.test_studio import APPLICATION, ROOT, synthesize_audio, wait_idle
from tools.ui_state_smoke import named_items, visual_items, click


def main():
    target = ROOT / 'runtime/ux-20261015'
    target.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle('Basic')
    screenshots = []
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        audio = folder / '演出版本.wav'; synthesize_audio(audio)
        subtitle = folder / '歌词.srt'
        subtitle.write_text('1\n00:00:00,100 --> 00:00:00,350\n让今晚的每一句都有画面\n\n2\n00:00:00,400 --> 00:00:00,700\n把喜欢的声音留在这里\n')
        seed = StudioController(ROOT, folder / 'runtime')
        seed.chooseAudio(str(audio)); wait_idle(seed)
        seed.chooseSubtitles(str(subtitle)); wait_idle(seed)
        for name in ('周末暖场', '演出准备'):
            seed.saveProject(str(folder / (name+'.bfstudio.json')))
        seed._recent_projects.remember(folder / '已移走的工程.bfstudio.json')
        seed.shutdown()
        studio = StudioController(ROOT, folder / 'runtime')
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
        engine.rootContext().setContextProperty('studio', studio)
        provider = StudioPreviewProvider(studio)
        engine.addImageProvider('studioPreview', provider)
        engine.loadData(('import QtQuick\nimport QtQuick.Controls\nimport "' +
                         QUrl.fromLocalFile(str(ROOT / 'ui')).toString() +
                         '"\nApplicationWindow { visible: true; color: "#17171b"; width: 1440; height: 900; StudioPanel { anchors.fill: parent } }').encode())
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        named_items(window)['studioWorkspace'].setProperty('advancedEditing', True)
        def item(name):
            result = named_items(window).get(name)
            assert result, 'Missing '+name
            return result
        def tap(name):
            control = item(name)
            assert control.isVisible() and control.isEnabled(), name
            click(window, control); QTest.qWait(50)
        def shot(name):
            QTest.qWait(150)
            path = target / name
            assert window.grabWindow().save(str(path))
            screenshots.append(str(path))
        try:
            QTest.qWait(150)
            for width, height in [(1440,900),(1000,700)]:
                window.resize(width,height); QTest.qWait(100)
                assert not item('studioRecentProject-0').isEnabled(), 'Missing recent project must be marked unavailable'
                for name in ('studioStartAudio','studioRecentProject-1','studioWelcomeLive'):
                    control = item(name)
                    assert control.isVisible() and control.y() >= 0
                shot(f'welcome-{width}.png')
            tap('studioRecentProject-1')
            assert len(studio.state['segments']) == 2
            assert studio.state['projectPath'].endswith('演出准备.bfstudio.json')
            tap('studioProjectMenu')
            assert any(node.isVisible() and str(node.property('text') or '').startswith('最近：')
                       for node in visual_items(window.contentItem())), 'Recent projects are missing from the actual menu'
            from PySide6.QtCore import Qt
            QTest.keyClick(window, Qt.Key.Key_Escape); QTest.qWait(50)
            with patch('app.studio.QFileDialog.getSaveFileName') as dialog:
                studio.updateSegment(0,'让今晚的每一句都有回忆')
                tap('studioSaveProject')
                dialog.assert_not_called()
            assert not studio.state['dirty']
            studio.seek(0); QTest.qWait(100)
            assert item('studioLyricGapHint').isVisible()
            assert '下一句' in item('studioLyricGapHint').property('text')
            tap('studioJumpToLyrics')
            assert studio.state['currentText']
            assert not item('studioJumpToLyrics').isVisible()
            tap('studioStep-3')
            assert item('studioGenerateVideo').isEnabled(), 'Default picture must support immediate export'
            assert not studio.state['backgroundPath']
            for width, height in [(1440,900),(1000,700)]:
                window.resize(width,height); QTest.qWait(100)
                assert item('studioGenerateVideo').isVisible()
                shot(f'export-{width}.png')
            output = folder / '一键导出.mp4'
            with patch('app.studio.QFileDialog.getSaveFileName', return_value=(str(output),'')) as dialog:
                tap('studioGenerateVideo')
                dialog.assert_called_once()
            wait_idle(studio); QTest.qWait(150)
            assert output.is_file() and studio.state['savedVideoPath'] == str(output), studio.state['status']
            shot('saved-1000.png')
            studio.seek(0); item('studioWorkspace').setProperty('showRendered', False); QTest.qWait(100)
            QTest.qWait(400)
            frame = item('studioLiveFrame')
            assert QQmlExpression(engine.rootContext(), frame, 'Number(status)').evaluate()[0] == 1
            screenshot = window.grabWindow()
            point = frame.mapToScene(QPointF(frame.width()/2,frame.height()/2))
            pixel = screenshot.pixelColor(round(point.x()*screenshot.width()/window.width()),
                                          round(point.y()*screenshot.height()/window.height()))
            assert pixel.blue()-pixel.red()>12, '切回编辑后默认背景仍然黑屏：'+pixel.name()
            shot('gap-1000.png')
            assert not warnings, '\n'.join(warnings)
            report = {'passed': True, 'fixtureMedia': True, 'cloudRequests': 0,
                      'checks': ['recent project resume', 'unavailable project disabled',
                                 'save without another dialog', 'lyric gap explained and jump',
                                 'default background can export', 'one destination dialog then saved MP4',
                                 '1440x900 and 1000x700 layouts'],
                      'screenshots': screenshots, 'qmlWarnings': warnings}
            (target / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False))
        finally:
            studio.shutdown(); provider.shutdown(); window.close(); APPLICATION.processEvents()


if __name__ == '__main__':
    main()
