"""Actual Qt controls with isolated fixtures and fake provider transport."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from scripts.repair_qt_metadata import repair_qt_metadata
repair_qt_metadata()
from PySide6.QtCore import QMetaObject,QObject,QPointF,Qt,QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.ai_settings import AISettings
from app.controller import Controller
from app.studio import StudioController
from app.studio_preview import StudioPreviewProvider
from tests.test_ai_providers import Transport,chat,KEY
from tests.test_ai_settings import FakeAIProcess
from tests.test_bailian import BailianTransport
from tests.test_bailian_shared import MemoryKeychain
from tests.test_studio import synthesize_audio,wait_idle
from tools.ui_state_smoke import click,visual_items
from worker.ai_tasks import run


def main(bailian=False,reference=False,onboarding=False):
    root=Path(__file__).resolve().parents[1]
    evidence = Path(os.environ['BAOFANG_UI_CHECK_DIR']).resolve() if os.environ.get('BAOFANG_UI_CHECK_DIR') else root/('runtime/api-onboarding-20261023' if onboarding else 'runtime/reference-ui-20261014' if reference else 'runtime/bailian-20261014' if bailian else 'runtime/api-domestic-20261014')
    evidence.mkdir(parents=True,exist_ok=True)
    application=QApplication.instance() or QApplication([])
    application.setFont(QFont('PingFang SC',13))
    QQuickStyle.setStyle('Basic')
    with tempfile.TemporaryDirectory(prefix='api-ui-',dir=evidence) as folder:
        runtime=Path(folder)
        audio=runtime/'实际版本测试.wav'
        synthesize_audio(audio)
        studio=StudioController(root,runtime)
        studio.chooseAudio(str(audio));wait_idle(studio)
        controller=Controller(root,runtime)
        processes=[]
        def factory(parent):
            process=FakeAIProcess(parent);processes.append(process);return process
        keychain=MemoryKeychain()
        model=AISettings(root,runtime,controller,studio,process_factory=factory,credential_store=keychain)
        if not bailian:model.selectProvider('speech','volc-asr')
        engine=QQmlApplicationEngine()
        for name,value in [('controller',controller),('studio',studio),('aiSettings',model)]:engine.rootContext().setContextProperty(name,value)
        provider=StudioPreviewProvider(studio)
        engine.addImageProvider('studioPreview',provider)
        warnings=[]
        engine.warnings.connect(lambda rows:warnings.extend(row.toString() for row in rows))
        engine.load(QUrl.fromLocalFile(str(root/'ui/Main.qml')))
        assert engine.rootObjects(),warnings
        window=engine.rootObjects()[0]
        panel=window.findChild(QObject,'apiSettingsPanel')
        panel.setProperty('unifiedMode',bailian)
        window.setProperty('currentPage',7)
        QTest.qWait(100)

        def item(name):
            nodes=[node for node in visual_items(window.contentItem()) if node.objectName()==name]
            assert nodes,'Missing '+name
            return next((node for node in nodes if node.isVisible()),nodes[-1])

        def reveal(node):
            ancestors=[];parent=node.parentItem()
            while parent:
                ancestors.append(parent);parent=parent.parentItem()
            for parent in ancestors:
                if parent.property('contentY') is None or parent.property('contentHeight') is None:continue
                content=parent.property('contentItem')
                if content is None:continue
                top=node.mapToItem(content,QPointF(0,0)).y()
                position=float(parent.property('contentY'))
                if top<position or top+node.height()>position+parent.height():
                    parent.setProperty('contentY',max(0,min(float(parent.property('contentHeight'))-parent.height(),top-12)))
            QTest.qWait(50)

        def tap(name):
            node=item(name);reveal(node)
            assert node.isVisible() and node.isEnabled(),name+' unavailable'
            click(window,node);QTest.qWait(60)

        def fill(name,text):
            node=item(name);reveal(node);click(window,node)
            QTest.keyClick(window,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
            node.setProperty('text',text)
            assert QMetaObject.invokeMethod(node,'textEdited',Qt.ConnectionType.DirectConnection)
            QTest.qWait(50)

        def finish(transport=None):
            process=processes[-1]
            output=run(process.request,KEY,transport=transport)
            process.queue(output);process.finish();QTest.qWait(70)

        def shot(name):
            path=evidence/name
            assert window.grabWindow().save(str(path))
            return str(path)

        shots=[];checks=[]
        try:
            if reference:
                from tests.test_reference_lyrics import Transport as ReferenceTransport
                original=[{'text':'今完的节奏跟随心跳。','startMs':100,'endMs':700,
                           'characters':[{'text':'今完的节奏跟随心跳。','startMs':100,'endMs':700}]}]
                from app.ai_settings import rows_digest
                studio.applyAIResult(original,'sha256:'+studio._sources['audio']['sha256'],rows_digest(studio.state['segments']))
                original=studio.state['segments']
                tap('apiTextTab')
                assert not item('bailianKeyField').isVisible() and not model.keys
                fill('apiReferenceTitle','今晚的节奏')
                tap('apiSearchReference');finish(ReferenceTransport())
                assert model.state['referenceProposal'] and studio.state['segments']==original
                for width,height in ((1440,900),(1000,700)):
                    window.resize(width,height);QTest.qWait(80)
                    for name in ('apiReferenceTitle','apiSearchReference','apiReferenceProof','apiChange-0','apiApplyProposal'):
                        node=item(name);reveal(node);pos=node.mapToScene(QPointF(0,0))
                        assert 0<=pos.x() and pos.x()+node.width()<=width+1,name+' clipped'
                    reveal(item('apiReferenceTitle'));shots.append(shot('reference-'+str(width)+'.png'))
                    reveal(item('apiChange-0'));shots.append(shot('review-'+str(width)+'.png'))
                tap('apiChange-0');assert not item('apiApplyProposal').isEnabled()
                tap('apiChange-0');tap('apiApplyProposal')
                after=studio.state['segments'][0]
                assert after['text']=='今晚的节奏跟随心跳。'
                assert after['characters'][0]['startMs']==100 and after['characters'][0]['endMs']==700
                studio.undo();assert studio.state['segments']==original
                controller.setOfflineOnly(True);QTest.qWait(50)
                tap('apiSearchReference');finish(ReferenceTransport())
                assert model.state['referenceCached'] and not model.keys
                assert processes[-1].environment.value('BAOFANG_AI_TASK_KEY')==''
                tap('apiReferenceCandidate-netease:123');finish()
                studio.updateSegment(0,'人工编辑后的歌词')
                QTest.qWait(40)
                assert model.state['proposalStale'] and not item('apiApplyProposal').isEnabled()
                assert not warnings,warnings
                report={'passed':True,'checks':['reference search and reviewed apply without any key','native word and sentence times unchanged','undo restores original ASR','offline cache and selected reference matching','manual edits reject stale proposals','1000x700 and 1440x900 layouts'],
                        'qmlWarnings':warnings,'screenshots':shots,'realCloudValidated':False,'cloudRequests':0}
                (evidence/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                print(json.dumps(report,ensure_ascii=False));return
            for width,height in ((1440,900),(1000,700)):
                window.resize(width,height);QTest.qWait(80)
                names=('appSettingsMenu','bailianKeyField','bailianSaveConfig','bailianAdvancedToggle') if bailian else ('appSettingsMenu','apiProviderCombo','apiModelField','apiBaseField','apiKeyField')
                for name in names:
                    node=item(name);reveal(node)
                    pos=node.mapToScene(QPointF(0,0))
                    assert 0<=pos.x() and pos.x()+node.width()<=width+1,name+' clipped horizontally'
                shots.append(shot('speech-'+str(width)+'.png'))
                if onboarding:
                    for name in ('bailianOpenConsole','bailianOpenKey','bailianOpenPricing','bailianServiceScope','bailianTestConnection','bailianRememberKey','bailianStorageStatus'):
                        node=item(name);reveal(node)
                        pos=node.mapToScene(QPointF(0,0))
                        assert 0<=pos.x() and pos.x()+node.width()<=width+1,name+' clipped'
                    reveal(item('bailianOpenConsole'));shots.append(shot('onboarding-'+str(width)+'.png'))
            if onboarding:
                from app.ai_settings import BAILIAN_PORTALS
                assert not item('bailianSaveConfig').isEnabled(),'Empty key cannot be used'
                fill('bailianKeyField',KEY)
                with patch('app.ai_settings.QDesktopServices.openUrl',return_value=True) as opened:
                    for name,destination in (('bailianOpenConsole','activate'),('bailianOpenKey','key'),('bailianOpenPricing','pricing')):
                        tap(name)
                        assert opened.call_args.args[0].toString()==BAILIAN_PORTALS[destination]
                        assert item('bailianKeyField').property('text')==KEY,'Browser link cleared draft'
                with patch('app.ai_settings.QDesktopServices.openUrl',return_value=False):
                    tap('bailianOpenKey')
                assert item('bailianPortalFallback').isVisible()
                assert item('bailianPortalFallback').property('readOnly')
                assert item('bailianPortalFallback').property('text')==BAILIAN_PORTALS['key']
                assert not processes,'Onboarding must not send model requests'
                fill('bailianKeyField','')
                reveal(item('bailianOpenConsole'));shots.append(shot('browser-fallback-1000.png'))
                checks.append('official browser destinations, copyable failure fallback, draft preserved, no automatic cloud requests, key-only onboarding at both sizes')
            key_name='bailianKeyField' if bailian else 'apiKeyField'
            save_name='bailianSaveConfig' if bailian else 'apiSaveConfig'
            test_name='bailianTestConnection' if bailian else 'apiTestConfig'
            if bailian:
                assert not item('bailianBaseField').isVisible(),'Advanced settings must start collapsed'
                tap('bailianAdvancedToggle')
                for name in ('bailianBaseField','bailianSpeechModel','bailianTextModel'):
                    assert item(name).isVisible()
                shots.append(shot('advanced-1000.png'))
                tap('bailianAdvancedToggle')
            if onboarding:
                assert not item('bailianRememberKey').property('checked')
                tap('bailianRememberKey')
            fill(key_name,KEY);tap(save_name)
            if onboarding:
                assert model.state['bailian']['remembered'] and keychain.values
                assert controller.preparation._key==KEY
                assert controller.preparation.view({})['config']['provider']=='bailian'
                restored=AISettings(root,runtime,controller,studio,credential_store=keychain)
                assert restored.state['speech']['keyReady'] and restored.state['text']['keyReady']
                restored.shutdown()
                tap('bailianRememberKey');tap(save_name)
                assert not keychain.values and model.state['bailian']['keyReady']
                tap('bailianRememberKey');tap(save_name)
                tap('bailianClearKey')
                assert not keychain.values and not controller.preparation._key
                assert not model.state['bailian']['keyReady'] and not item('bailianRememberKey').property('checked')
                fill(key_name,KEY);tap(save_name)
                checks.append('opt-in Keychain control, restart recovery, forget keeps session, clear removes shared key and stored copy')
            assert model.state['speech']['keyReady'] and item(key_name).property('text')==''
            if bailian:assert model.state['text']['keyReady'] and len(model.keys)==1
            assert KEY not in (runtime/'ai-profiles.json').read_text()
            tap(test_name);finish(BailianTransport() if bailian else None)
            assert ('待歌曲实测' if bailian else '尚未上传') in model.state['speech']['result']
            tap('apiPlanSpeech');finish()
            assert model.state['plan']['versionId'].startswith('sha256:')
            tap('apiStartSpeech')
            transport=BailianTransport(pending=True) if bailian else Transport({'result':{'text':'今完的节奏','utterances':[{'text':'今完的节奏','start_time':50,'end_time':700}]}},{'x-api-status-code':'20000000'})
            finish(transport)
            if bailian:
                assert model.state['canQuery'] and model.state['phase']=='polling'
                tap('apiQuerySpeech');finish(BailianTransport())
            assert not studio.state['segments'],'Recognition applied before review'
            tap('apiApplyProposal')
            assert studio.state['segments'][0]['text']=='今完的节奏'
            assert studio.state['segments'][0]['startMs']==50
            checks.append('real UI config save, password field clearing, local-only speech validation, file plan, provider contract and reviewed recognition apply')
            tap('apiTextTab')
            tap('apiModelCorrectionMode')
            if not bailian:fill(key_name,KEY);tap(save_name)
            else:assert model.state['text']['keyReady'],'Second key must not be required'
            window.resize(1440,900);QTest.qWait(80)
            reveal(item(key_name));shots.append(shot('text-1440.png'))
            tap('apiCorrectLyrics')
            text_transport=Transport(chat([{'index':0,'text':'今晚的节奏'}]))
            finish(text_transport)
            assert studio.state['segments'][0]['text']=='今完的节奏'
            reveal(item('apiApplyProposal'));shots.append(shot('correction-review.png'))
            tap('apiChange-0')
            assert not item('apiApplyProposal').isEnabled()
            tap('apiChange-0');tap('apiApplyProposal')
            assert studio.state['segments'][0]['text']=='今晚的节奏'
            assert studio.state['segments'][0]['startMs']==50 and studio.state['segments'][0]['endMs']==700
            studio.undo();assert studio.state['segments'][0]['text']=='今完的节奏'
            checks.append('text correction transmits only text, preview keeps original, individual selection works, apply retains times and studio undo restores original')
            controller.setOfflineOnly(True);QTest.qWait(60)
            assert not item('apiCorrectLyrics').isEnabled() and not item(test_name).isEnabled()
            checks.append('shared offline performance mode disables model tests, uploads and correction requests')
            window.resize(1000,700);QTest.qWait(80)
            reveal(item(key_name));shots.append(shot('text-1000.png'))
            if bailian:checks.append('one Bailian Key serves ASR and correction; advanced settings start collapsed; asynchronous original task query does not upload again')
            assert not warnings,'\n'.join(warnings)
            report={'passed':True,'checks':checks,'qmlWarnings':warnings,'screenshots':shots,'realCloudValidated':False,'realKeysRead':False,'cloudRequests':0}
            (evidence/'ui-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(report,ensure_ascii=False))
        finally:
            model.shutdown();controller.shutdown();provider.shutdown();studio.shutdown();window.close();application.processEvents()


if __name__=='__main__':main(any(flag in sys.argv for flag in ('--bailian','--reference','--onboarding')),'--reference' in sys.argv,'--onboarding' in sys.argv)
