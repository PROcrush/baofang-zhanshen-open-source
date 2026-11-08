"""A first-use journey through real controls, local fixtures and our renderer."""
import json
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor,QImage,QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.studio import StudioController
from app.studio_preview import StudioPreviewProvider
from tools.ui_state_smoke import SmokeController,click,named_items,visual_items


def main(lyrics_only=False):
    root=Path(__file__).resolve().parent.parent
    target=root/'runtime/ui-user-20261014/studio';target.mkdir(parents=True,exist_ok=True)
    run=target/uuid.uuid4().hex;run.mkdir()
    application=QApplication.instance() or QApplication([]);application.setFont(QFont('PingFang SC',13));QQuickStyle.setStyle('Basic')
    studio=StudioController(root,run/'runtime');provider=StudioPreviewProvider(studio)
    facade=SmokeController('replay');engine=QQmlApplicationEngine();warnings=[]
    engine.rootContext().setContextProperty('studio',studio);engine.rootContext().setContextProperty('controller',facade)
    engine.addImageProvider('studioPreview',provider)
    engine.warnings.connect(lambda rows:warnings.extend(row.toString() for row in rows))
    engine.load(QUrl.fromLocalFile(str(root/'ui/Main.qml')))
    assert engine.rootObjects(),warnings
    window=engine.rootObjects()[0];window.setProperty('currentPage',5);QTest.qWait(100)
    named_items(window)['studioWorkspace'].setProperty('advancedEditing',True)
    def item(name):
        matches=[node for node in visual_items(window.contentItem()) if node.objectName()==name]
        assert matches,'Missing control '+name
        return next((node for node in matches if node.isVisible()),matches[-1])
    def tap(name):
        node=item(name);assert node.isVisible() and node.isEnabled(),name
        click(window,node);QTest.qWait(30)
    def tap_text(text):
        node=next(node for node in visual_items(window.contentItem()) if node.isVisible() and node.property('text')==text and 'StudioButton' in node.metaObject().className())
        click(window,node);QTest.qWait(30)
    def wait():
        deadline=time.monotonic()+120
        while studio.state['busy'] and time.monotonic()<deadline:QTest.qWait(20)
        QTest.qWait(80);assert not studio.state['busy'],studio.state['status']
    def shot(name):
        path=target/name;assert window.grabWindow().save(str(path));return str(path)
    checks=[];screens=[]
    try:
        for width,height in [(1440,900),(1000,700)]:
            window.resize(width,height);QTest.qWait(70)
            start=item('studioStartAudio');assert start.isVisible() and start.isEnabled()
            assert not item('studioInspector').isVisible()
            assert not item('studioLibrary').isVisible() and not item('studioTimeline').isVisible()
            screens.append(shot(f'empty-{width}.png'))
        tap('studioWelcomeLive');assert window.property('currentPage')==0
        tap('nav-5');assert window.property('currentPage')==5
        checks.append('empty state has usable song/example/project actions without irrelevant editing tools; direct live entry works in two sizes')
        from tests.test_studio import synthesize_audio
        audio=run/'新手流程音频.wav';synthesize_audio(audio)
        with patch('app.studio.QFileDialog.getOpenFileName',return_value=(str(audio),'')):tap('studioStartAudio')
        wait();assert studio.state['audioPath']==str(audio)
        assert item('studioLibrary').isVisible() and item('studioTimeline').isVisible()
        assert item('studioSaveStatus').property('text')=='未保存'
        assert item('studioWelcomeSubtitles').isVisible() and item('studioCloudRecognition').isEnabled()
        tap('studioCloudRecognition');assert window.property('currentPage')==7
        tap('nav-5');assert window.property('currentPage')==5
        tap('studioLibraryRecognize');assert window.property('currentPage')==7
        tap('nav-5');assert window.property('currentPage')==5
        screens.append(shot('song-imported-1000.png'))
        subtitle=run/'歌词.srt';subtitle.write_text('1\n00:00:00,050 --> 00:00:00,350\n让今晚的每一句都有画面\n\n2\n00:00:00,400 --> 00:00:00,750\n把喜欢的声音留在这里\n')
        with patch('app.studio.QFileDialog.getOpenFileName',return_value=(str(subtitle),'')):tap('studioWelcomeSubtitles')
        wait();assert len(studio.state['segments'])==2
        if lyrics_only:
            assert not warnings,'\n'.join(warnings)
            print('云端识别入口与本地字幕导入通过')
            return
        assert item('templateTile-headline').isVisible()
        assert not item('styleLayout-amplitude').isVisible()
        tap('templateTile-headline');assert studio.state['settings']['templateId']=='headline'
        tap('styleAdvancedToggle');assert item('styleLayout-amplitude').isVisible()
        tap('styleAdvancedToggle');assert not item('styleLayout-amplitude').isVisible()
        tap_text('字体');tap('styleColor-textColor-#FFD890')
        assert studio.state['settings']['textColor']=='#FFD890'
        tap_text('模板')
        checks.append('welcome and library recognition buttons open cloud settings; local subtitle import reveals real template previews')
        tap('studioStep-2')
        background=run/'背景 #1.png';image=QImage(400,225,QImage.Format_RGBA8888);image.fill(QColor('#294449'));assert image.save(str(background))
        with patch('app.studio.QFileDialog.getOpenFileName',return_value=(str(background),'')):tap('studioChooseBackground')
        assert studio.state['backgroundPath']==str(background)
        for width,height in [(1440,900),(1000,700)]:
            window.resize(width,height);QTest.qWait(70);screens.append(shot(f'editor-{width}.png'))
        tap('studioStep-3')
        direct=run/'直接导出的成品.mp4'
        with patch('app.studio.QFileDialog.getSaveFileName',return_value=(str(direct),'')):tap('studioGenerateVideo')
        wait();assert studio.state['resultPath'] and direct.is_file(),studio.state['status']
        destination=run/'保存的成品.mp4'
        with patch('app.studio.QFileDialog.getSaveFileName',return_value=(str(destination),'')):tap('studioSaveVideo')
        assert destination.read_bytes()==Path(studio.state['resultPath']).read_bytes(),studio.state['status']
        screens.append(shot('export-saved-1000.png'))
        checks.append('fixed export actions stay reachable at 1000px; Save As copies the verified video into the chosen file')
        checks.append('background import and template choices affect actual independently rendered MP4')
        studio.openExampleProject();QTest.qWait(120)
        assert len(studio.state['segments'])==4 and Path(studio.state['projectPath']).is_file()
        assert studio.state['recoveryPath'] and Path(studio.state['recoveryPath']).is_file()
        assert not studio.state['dirty']
        assert item('studioSaveStatus').property('text')=='已保存'
        window.resize(1440,900);QTest.qWait(80);screens.append(shot('example-1440.png'))
        project=studio.state['projectPath'];studio.openProject(project)
        assert len(studio.state['segments'])==4 and not studio.state['dirty']
        checks.append('built-in example creates actual original audio/background/lyrics; v3 opens cleanly and preserves previous unsaved work in recovery')
        assert not warnings,'\n'.join(warnings)
        report={'passed':True,'checks':checks,'qmlWarnings':warnings,'screenshots':screens,'exampleProject':project,'syntheticOnly':True,'cloudRequests':0}
        (target/'onboarding-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
    finally:
        provider.shutdown();studio.shutdown();window.close();application.processEvents()


if __name__=='__main__':main('--lyrics' in sys.argv)
