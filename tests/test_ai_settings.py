import copy
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, QProcessEnvironment, Signal

from app.ai_settings import AISettings, rows_digest
from app.storage import inspect_audio
from test_preparation import FakeProcess

KEY='TEST_ONLY_FAKE_AI_KEY_NOT_SENT'


class FakeAIProcess(FakeProcess):
    @property
    def request(self):
        return json.loads(Path(self.arguments[self.arguments.index('--request')+1]).read_text())


class Legacy:
    offline_only=False
    def configure_inline(self,*args):self.config=args
    def clear_key(self):self.config=None


class ControllerStub(QObject):
    stateChanged=Signal()
    def __init__(self):
        super().__init__()
        self.preparation=Legacy()


class StudioStub:
    def __init__(self,track):
        self._sources={'audio':{'path':track['path'],'sha256':track['versionId'][7:]}}
        self.value={'busy':False,'durationMs':1000,'audioTitle':'测试歌曲','segments':[{'text':'今完的节奏','startMs':0,'endMs':900,'characters':[{'text':'今完的节奏','startMs':0,'endMs':900}]}]}
    @property
    def state(self):return copy.deepcopy(self.value)
    def applyAIResult(self,rows,version,digest):
        assert rows_digest(self.value['segments'])==digest
        self.value['segments']=rows


class AISettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve()
        path=self.root/'测试.wav'
        with wave.open(str(path),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(bytes(16000))
        self.track=inspect_audio(path)
        self.controller=ControllerStub()
        self.studio=StudioStub(self.track)
        self.processes=[]
        def factory(parent):
            process=FakeAIProcess(parent);self.processes.append(process);return process
        self.model=AISettings(self.root,self.root/'runtime',self.controller,self.studio,process_factory=factory)
        self.addCleanup(self.model.shutdown)

    def configure(self,provider='qwen',base='https://dashscope.aliyuncs.com/compatible-mode/v1',model='qwen-plus',key=KEY):
        return self.model.configure('text',provider,base,model,key,0)

    def complete(self,payload):
        process=self.processes[-1]
        process.queue(payload)
        process.finish()

    def test_bailian_portals_open_only_public_destinations_without_calls(self):
        from app.ai_settings import BAILIAN_PORTALS
        self.assertTrue(self.configure())
        before=self.model.state['text']['result']
        with patch('app.ai_settings.QDesktopServices.openUrl',return_value=True) as opened:
            for name,url in BAILIAN_PORTALS.items():
                self.assertTrue(self.model.openBailianPortal(name))
                self.assertEqual(opened.call_args.args[0].toString(),url)
                self.assertNotIn(KEY,url)
        self.assertEqual(self.processes,[])
        self.assertEqual(self.model.state['text']['result'],before)
        self.assertNotIn(KEY,json.dumps(self.model.state))

    def test_failed_browser_launch_exposes_copyable_public_url(self):
        from app.ai_settings import BAILIAN_PORTALS
        with patch('app.ai_settings.QDesktopServices.openUrl',return_value=False):
            self.assertFalse(self.model.openBailianPortal('key'))
        self.assertEqual(self.model.state['portalFallbackUrl'],BAILIAN_PORTALS['key'])
        self.assertIn('复制',self.model.state['portalNotice'])
        with patch('app.ai_settings.QDesktopServices.openUrl',return_value=True):
            self.assertTrue(self.model.openBailianPortal('key'))
        self.assertEqual(self.model.state['portalFallbackUrl'],'')

    def test_portal_rejects_arbitrary_urls(self):
        with patch('app.ai_settings.QDesktopServices.openUrl') as opened:
            self.assertFalse(self.model.openBailianPortal('https://example.org/?key='+KEY))
        opened.assert_not_called()
        self.assertEqual(self.processes,[])
        self.assertNotIn(KEY,json.dumps(self.model.state))

    def test_keys_never_enter_state_settings_or_worker_files(self):
        self.assertTrue(self.configure())
        self.assertTrue(self.model.state['text']['keyReady'])
        self.assertNotIn(KEY,json.dumps(self.model.state))
        self.assertNotIn(KEY,(self.model.runtime/'ai-profiles.json').read_text())
        self.model.correctLyrics()
        process=self.processes[-1]
        self.assertNotIn(KEY,json.dumps(process.request))
        self.assertEqual(process.environment.value('BAOFANG_AI_TASK_KEY'),KEY)
        self.assertEqual(process.environment.value('PYTHONDONTWRITEBYTECODE'),'1')
        self.assertEqual(process.environment.value('PYTHONNOUSERSITE'),'1')
        self.assertNotIn(KEY,' '.join(process.arguments))
        other=AISettings(self.root,self.model.runtime,self.controller,self.studio)
        self.addCleanup(other.shutdown)
        self.assertFalse(other.state['text']['keyReady'])

    def test_key_cannot_be_reused_with_another_endpoint(self):
        self.assertTrue(self.configure('compatible-text','https://first.example/v1','test'))
        self.assertFalse(self.configure('compatible-text','https://second.example/v1','test',key=''))
        self.assertEqual(self.model.state['text']['baseUrl'],'https://first.example/v1')

    def test_model_switch_preserves_separate_credentials(self):
        self.configure()
        self.configure('deepseek','https://api.deepseek.com','deepseek-flash','OTHER_FAKE_KEY')
        self.model.selectProvider('text','qwen')
        self.assertTrue(self.model.state['text']['keyReady'])
        self.assertEqual(self.model.state['text']['model'],'qwen-plus')

    def test_model_response_is_a_reviewable_proposal_until_selected_apply(self):
        self.configure()
        self.model.correctLyrics()
        self.complete({'phase':'review','detail':'测试建议','proposal':{'rows':[], 'changes':[{'index':0,'before':'今完的节奏','after':'今晚的节奏','selected':True}]}})
        self.assertEqual(self.studio.state['segments'][0]['text'],'今完的节奏')
        self.model.selectChange(0,False)
        self.model.apply()
        self.assertEqual(self.studio.state['segments'][0]['text'],'今完的节奏')
        self.model.selectChange(0,True)
        self.model.apply()
        self.assertEqual(self.studio.state['segments'][0]['text'],'今晚的节奏')
        self.assertEqual(self.studio.state['segments'][0]['endMs'],900)
        self.assertNotIn('characters',self.studio.state['segments'][0])
        self.assertFalse(self.model.state['hasProposal'])

    def test_reference_search_needs_no_key_and_preserves_actual_character_clock(self):
        from worker.lyric_reference import reference
        from worker.reference_match import match
        self.model.searchReference('测试歌曲','歌手')
        process=self.processes[-1]
        self.assertEqual(process.request['action'],'search-reference')
        self.assertEqual(process.environment.value('BAOFANG_AI_TASK_KEY'),'')
        self.studio.value['segments'][0]['text']='今完的节奏跟随心跳'
        self.studio.value['segments'][0]['characters'][0]['text']='今完的节奏跟随心跳'
        self.model.cancel()
        self.model.searchReference('测试歌曲','歌手')
        before=copy.deepcopy(self.studio.value['segments'])
        ref=reference('netease',123,'测试歌曲','歌手','今晚的节奏跟随心跳')
        p=match(before,ref)
        self.complete({'phase':'review','candidates':[ref],'proposal':p})
        self.assertEqual(self.studio.value['segments'],before)
        self.model.apply()
        after=self.studio.value['segments'][0]
        self.assertEqual(after['text'],'今晚的节奏跟随心跳')
        self.assertEqual(after['characters'][0]['startMs'],before[0]['characters'][0]['startMs'])
        self.assertEqual(after['characters'][0]['endMs'],before[0]['characters'][0]['endMs'])
        self.assertEqual(after['characters'][0]['text'],after['text'])
        audit=json.loads(next((self.model.runtime/'reference-audits').glob('*.json')).read_text())
        self.assertEqual(audit['originalRows'],before)
        self.assertFalse(audit['originalClockUsed'])

    def test_offline_reference_worker_has_no_secret_and_candidate_becomes_stale(self):
        from worker.lyric_reference import reference
        self.configure()
        self.controller.preparation.offline_only=True
        self.model.searchReference('测试歌曲','')
        process=self.processes[-1]
        self.assertTrue(process.request['offline'])
        self.assertEqual(process.environment.value('BAOFANG_AI_TASK_KEY'),'')
        ref=reference('netease',123,'测试歌曲','歌手','今晚的节奏跟随心跳')
        self.complete({'phase':'choose-reference','candidates':[ref],'cached':True})
        self.assertTrue(self.model.state['referenceCached'])
        self.model.matchReference(ref['id'])
        self.assertEqual(self.processes[-1].environment.value('BAOFANG_AI_TASK_KEY'),'')
        self.complete({'phase':'review','proposal':{'changes':[]}})
        self.studio.value['segments'][0]['text']='人工新歌词'
        count=len(self.processes)
        self.model.matchReference(ref['id'])
        self.assertEqual(len(self.processes),count)
        self.assertIn('已经变化',self.model.state['detail'])

    def test_reference_cannot_inject_original_song_or_changed_word_times(self):
        from worker.lyric_reference import reference
        from worker.reference_match import match
        self.studio.value['segments'][0]['text']='今完的节奏跟随心跳'
        self.studio.value['segments'][0]['characters'][0]['text']='今完的节奏跟随心跳'
        before=copy.deepcopy(self.studio.value['segments'])
        ref=reference('netease',123,'测试','歌手','今晚的节奏跟随心跳')
        for field in ('startMs','characters'):
            self.model.searchReference('测试','')
            p=match(before,ref)
            if field=='startMs':p['rows'][0]['startMs']=100
            else:p['rows'][0]['characters'][0]['startMs']=100
            self.complete({'phase':'review','proposal':p})
            self.model.apply()
            self.assertEqual(self.studio.value['segments'],before)
            self.assertEqual(self.model.state['phase'],'failed')

    def test_new_edits_block_stale_ai_proposals(self):
        self.configure();self.model.correctLyrics()
        self.complete({'phase':'review','proposal':{'changes':[{'index':0,'before':'今完的节奏','after':'今晚的节奏','selected':True}]}})
        self.studio.value['segments'][0]['text']='人工已改的歌词'
        self.model.apply()
        self.assertEqual(self.studio.state['segments'][0]['text'],'人工已改的歌词')
        self.assertEqual(self.model.state['phase'],'failed')

    def test_offline_mode_blocks_new_requests_and_cancels_inflight_cloud(self):
        self.configure();self.model.correctLyrics()
        process=self.processes[-1]
        self.controller.preparation.offline_only=True
        self.controller.stateChanged.emit()
        self.assertTrue(process.killed)
        count=len(self.processes)
        self.model.correctLyrics();self.model.test('text');self.model.planSpeech()
        self.assertEqual(len(self.processes),count)
        self.assertTrue(self.model.state['offline'])

    def test_inline_302_configuration_feeds_existing_preparation(self):
        self.assertTrue(self.model.configure('speech','302-singing','https://api.302ai.cn','doubao-vc-singing',KEY,0.5))
        self.assertEqual(self.controller.preparation.config,(KEY,'domestic',0.5))
        self.model.clearKey('speech')
        self.assertIsNone(self.controller.preparation.config)

    def test_repeated_clock_ticks_do_not_rebuild_model_settings(self):
        changes=[]
        self.model.stateChanged.connect(lambda:changes.append(True))
        for _ in range(100):self.controller.stateChanged.emit()
        self.assertEqual(changes,[])

    def test_api_302_calls_share_preparation_budget_reservations(self):
        from app.preparation import PreparationManager
        from app.storage import Library
        from test_preparation import FakeTimer
        from worker.ai_tasks import run
        library=Library(self.root/'library')
        self.addCleanup(library.close)
        with patch('app.preparation.QTimer',FakeTimer):
            legacy=PreparationManager(self.root,self.model.runtime,library,{},lambda:None,lambda *_:None)
        self.addCleanup(legacy.shutdown)
        self.controller.preparation=legacy
        self.model.configure('speech','302-singing','https://api.302ai.cn','doubao-vc-singing',KEY,0.02)
        self.model.planSpeech()
        plan=run(self.processes[-1].request)['plan']
        self.complete({'phase':'planned','plan':plan})
        self.model.startSpeech()
        self.assertAlmostEqual(legacy.reservations[self.track['versionId']],0.011)
        self.complete({'phase':'unknown','detail':'测试保留提交记录'})
        content=bytearray(Path(self.track['path']).read_bytes());content[-1]=1
        next_path=self.root/'另一版本.wav';next_path.write_bytes(content)
        next_track=inspect_audio(next_path)
        self.studio._sources['audio']={'path':next_track['path'],'sha256':next_track['versionId'][7:]}
        self.model.planSpeech()
        plan=run(self.processes[-1].request)['plan']
        self.complete({'phase':'planned','plan':plan})
        before=len(self.processes)
        self.model.startSpeech()
        self.assertEqual(len(self.processes),before)
        self.assertIn('剩余额度不足',self.model.state['detail'])

    def test_bailian_uses_one_key_for_both_tasks_and_keeps_capability_results_separate(self):
        self.assertTrue(self.model.configureBailian(KEY,'https://dashscope.aliyuncs.com','fun-asr','qwen-plus'))
        self.assertTrue(self.model.state['speech']['keyReady'])
        self.assertTrue(self.model.state['text']['keyReady'])
        self.assertEqual(len(self.model.keys),1)
        self.model.testBailian()
        self.assertEqual(self.processes[-1].environment.value('BAOFANG_AI_TASK_KEY'),KEY)
        self.complete({'phase':'checked','capabilities':{'speech':'上传权限已核验，识别待歌曲实测','text':'文字模型连接成功'}})
        self.assertIn('待歌曲实测',self.model.state['bailian']['speechResult'])
        self.assertIn('连接成功',self.model.state['bailian']['textResult'])
        self.assertNotIn(KEY,json.dumps(self.model.state))
        self.model.clearBailianKey()
        self.assertFalse(self.model.state['speech']['keyReady'])
        self.assertFalse(self.model.state['text']['keyReady'])

    def test_bailian_workspace_change_cannot_reuse_original_key_or_package_key(self):
        self.model.configureBailian(KEY,'https://dashscope.aliyuncs.com','fun-asr','qwen-plus')
        workspace='https://test-space.cn-beijing.maas.aliyuncs.com'
        self.assertFalse(self.model.configureBailian('',workspace,'fun-asr','qwen-plus'))
        self.assertFalse(self.model.configureBailian('sk-sp-FAKE',workspace,'fun-asr','qwen-plus'))
        self.assertTrue(self.model.configureBailian('OTHER_FAKE_KEY',workspace,'fun-asr','qwen-plus'))
        self.assertEqual(self.model.state['text']['baseUrl'],workspace+'/compatible-mode/v1')
        self.assertEqual(self.model._key_slot(self.model._config('speech')),self.model._key_slot(self.model._config('text')))

    def test_bailian_task_can_resume_after_restart_without_saved_key(self):
        from worker.ai_tasks import run
        self.model.configureBailian(KEY,'https://dashscope.aliyuncs.com','fun-asr','qwen-plus')
        self.model.planSpeech();plan=run(self.processes[-1].request)['plan']
        self.complete({'phase':'planned','plan':plan})
        self.model.startSpeech()
        request=copy.deepcopy(self.processes[-1].request)
        self.complete({'phase':'polling','detail':'百炼正在识别'})
        for path in self.model.runtime.rglob('*.json'):self.assertNotIn(KEY,path.read_text())
        restarted=AISettings(self.root,self.model.runtime,self.controller,self.studio,process_factory=lambda parent:FakeAIProcess(parent))
        self.addCleanup(restarted.shutdown)
        self.assertTrue(restarted.state['canQuery'])
        self.assertFalse(restarted.state['bailian']['keyReady'])
        restarted.configureBailian(KEY,'https://dashscope.aliyuncs.com','fun-asr','qwen-plus')
        restarted.querySpeech()
        self.assertEqual(restarted.process.request['action'],'query')
        self.assertEqual(restarted.process.request['receipt'],request['receipt'])
        self.assertEqual(restarted.process.request['plan'],request['plan'])

    def test_bailian_new_plan_recovers_same_version_task_instead_of_resubmitting(self):
        from worker.ai_tasks import run
        self.model.configureBailian(KEY,'https://dashscope.aliyuncs.com','fun-asr','qwen-plus')
        self.model.planSpeech();self.complete({'phase':'planned','plan':run(self.processes[-1].request)['plan']})
        self.model.startSpeech();original=self.processes[-1].request
        Path(original['receipt']).write_text(json.dumps({'state':'polling'}))
        self.complete({'phase':'polling','detail':'百炼正在识别'})
        self.model.planSpeech();self.complete({'phase':'planned','plan':run(self.processes[-1].request)['plan']})
        self.model.startSpeech();restored=self.processes[-1].request
        self.assertEqual(restored['action'],'query')
        self.assertEqual(restored['plan']['versionId'],original['plan']['versionId'])
        self.assertEqual(restored['snapshot'],original['snapshot'])

    def test_bailian_start_failure_before_worker_submit_can_be_confirmed_again(self):
        from worker.ai_tasks import run
        self.model.configureBailian(KEY,'https://dashscope.aliyuncs.com','fun-asr','qwen-plus')
        self.model.planSpeech();self.complete({'phase':'planned','plan':run(self.processes[-1].request)['plan']})
        self.model.startSpeech();self.complete({'phase':'failed','detail':'后台尚未启动'})
        self.model.planSpeech();self.complete({'phase':'planned','plan':run(self.processes[-1].request)['plan']})
        self.model.startSpeech()
        self.assertEqual(self.processes[-1].request['action'],'recognize')

    def test_bailian_quota_rejection_can_be_reconfirmed_after_new_plan(self):
        from worker.ai_tasks import run
        self.model.configureBailian(KEY,'https://dashscope.aliyuncs.com','fun-asr','qwen-plus')
        self.model.planSpeech();self.complete({'phase':'planned','plan':run(self.processes[-1].request)['plan']})
        self.model.startSpeech();request=self.processes[-1].request
        Path(request['receipt']).write_text(json.dumps({'state':'quota_blocked'}))
        self.complete({'phase':'failed','detail':'免费额度不足'})
        self.model.planSpeech();self.complete({'phase':'planned','plan':run(self.processes[-1].request)['plan']})
        self.model.startSpeech()
        self.assertEqual(self.processes[-1].request['action'],'recognize')


if __name__=='__main__':unittest.main()
