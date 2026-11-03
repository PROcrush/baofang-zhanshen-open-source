"""Real Qt + subprocess preflight + batch/cache flow, with fake cloud only."""
import json
import os
import socket
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QObject,QPointF,QMetaObject,Qt,QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.ai_settings import AISettings
from app.controller import Controller
from app.studio import StudioController
from app.studio_preview import StudioPreviewProvider
from tests.test_bailian import BailianTransport,KEY
from tests.test_bailian_shared import MemoryKeychain
from tests.test_preparation import FakeProcess
from tools.ui_state_smoke import click,visual_items
from worker.bailian_preparation import run


def main():
    root=Path(__file__).resolve().parents[1]
    evidence = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root/'runtime/bailian-shared-20261023'; evidence.mkdir(parents=True,exist_ok=True)
    app=QApplication([]);app.setFont(QFont('PingFang SC',13));QQuickStyle.setStyle('Basic')
    with tempfile.TemporaryDirectory(dir=evidence,prefix='batch-ui-') as folder:
        runtime=Path(folder);controller=Controller(root,runtime);studio=StudioController(root,runtime)
        model=AISettings(root,runtime,controller,studio,credential_store=MemoryKeychain())
        provider=StudioPreviewProvider(studio);engine=QQmlApplicationEngine();engine.addImageProvider('studioPreview',provider)
        for name,obj in [('controller',controller),('studio',studio),('aiSettings',model)]:engine.rootContext().setContextProperty(name,obj)
        warnings=[];engine.warnings.connect(lambda rows:warnings.extend(row.toString() for row in rows))
        engine.load(QUrl.fromLocalFile(str(root/'ui/Main.qml')));assert engine.rootObjects(),warnings
        window=engine.rootObjects()[0]
        def wait(predicate):
            for _ in range(600):
                if predicate():return
                QTest.qWait(10)
            raise AssertionError('Timed out waiting for batch UI')
        def item(name):
            return next(node for node in visual_items(window.contentItem()) if node.objectName()==name and node.isVisible())
        def tap(name):
            node=item(name);assert node.isEnabled(),name
            click(window,node);QTest.qWait(50)
        try:
            with patch('socket.create_connection',side_effect=AssertionError('No cloud network permitted')):
                for index in range(2):
                    path=runtime/('合成测试 '+str(index)+'.wav')
                    with wave.open(str(path),'wb') as stream:
                        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000)
                        stream.writeframes((index+1).to_bytes(2,'little')*16000)
                    controller.index_audio(str(path))
                wait(lambda:len(controller.file_proofs)==2)
                assert model.configureBailian(KEY,'https://dashscope.aliyuncs.com','fun-asr','qwen-plus')
                window.setProperty('currentPage',4);QTest.qWait(80)
                budget=item('recognitionBudgetField');budget.setProperty('text','1')
                QMetaObject.invokeMethod(budget,'editingFinished',Qt.ConnectionType.DirectConnection)
                tap('recognitionAllowSubmission')
                assert controller.preparation.budget==1 and controller.preparation.enabled
                assert '百炼' in item('recognitionConfigStatus').property('text')
                window.setProperty('currentPage',1);QTest.qWait(80);tap('offlineBatchPlan')
                wait(lambda:controller.preparation.batch_view()['status']=='planned')
                view=controller.preparation.batch_view();assert view['planned']==2 and view['canStart'],view
                assert '元' in item('offlineBatchSummary').property('text')
                assert 'PTC' not in item('offlineBatchSummary').property('text')
                screenshots=[]
                for width,height in ((1440,900),(1000,700)):
                    window.resize(width,height);QTest.qWait(80)
                    for name in ('offlineBatchReview','offlineBatchStart','offlineBatchSummary'):
                        node=item(name);pos=node.mapToScene(QPointF(0,0))
                        assert pos.x()>=0 and pos.x()+node.width()<=width+1,name
                    image=evidence/('batch-'+str(width)+'.png');assert window.grabWindow().save(str(image));screenshots.append(str(image))
                    tap('offlineBatchReview')
                    assert item('offlineBatchReviewList').property('count')==2
                    text=' '.join(str(node.property('text') or '') for node in visual_items(window.contentItem()) if node.isVisible())
                    assert '新任务预估' in text and 'PTC' not in text
                    image=evidence/('batch-review-'+str(width)+'.png');assert window.grabWindow().save(str(image));screenshots.append(str(image))
                    tap('offlineBatchReviewClose')
                processes=[]
                def factory(parent):
                    process=FakeProcess(parent);processes.append(process);return process
                controller.preparation.process_factory=factory
                tap('offlineBatchStart');assert len(processes)==1
                tap('offlineBatchPause')
                def finish():
                    process=processes[-1];receipt=process.arguments[process.arguments.index('--receipt')+1]
                    result=run(process.action,process.request,receipt,KEY,transport=BailianTransport(),poll_limit=1)
                    process.queue(result);process.finish();QTest.qWait(70)
                finish();assert len(processes)==1
                assert controller.preparation.batch_view()['ready']==1
                tap('offlineBatchStart');assert len(processes)==2
                finish();assert controller.preparation.batch_view()['ready']==2
                assert controller.preparation.batch_view()['status']=='completed'
                for track in controller.preparation.tracks.values():
                    timeline=controller.library.timeline(track['versionId'])
                    assert timeline['segments'][0]['words'][0]['startMs']==50
                for path in runtime.rglob('*.json'):assert KEY not in path.read_text()
                assert not warnings,warnings
                result={'passed':True,'checks':['one Key config reaches batch','real local QProcess preflight of two distinct files','CNY totals and two window sizes','pause finishes current song without dispatching next','resume completes exact-file lyric cache with original word times'],
                        'qmlWarnings':warnings,'screenshots':screenshots,'cloudRequests':0,'realKeysRead':False}
                (evidence/'batch-ui.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
        finally:
            model.shutdown();controller.shutdown();provider.shutdown();studio.shutdown();window.close();app.processEvents()


if __name__=='__main__':main()
