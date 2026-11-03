import copy
import json
import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from app.storage import inspect_audio
from worker.ai_providers import ProviderError, profile, speech_result
from worker.ai_tasks import run

KEY='TEST_ONLY_BAILIAN_KEY_NOT_SENT'
POLICY={'data':{'policy':'FAKE_POLICY','signature':'FAKE_SIGNATURE','oss_access_key_id':'FAKE_OSS_ID',
    'upload_dir':'dashscope-instant/test/audio','upload_host':'https://dashscope-file-test.oss-cn-beijing.aliyuncs.com',
    'max_file_size_mb':100,'x_oss_object_acl':'private','x_oss_forbid_overwrite':'true'}}
TRANSCRIPT={'transcripts':[{'channel_id':0,'text':'今完的节奏','sentences':[{'begin_time':50,'end_time':700,
    'text':'今完的节奏','words':[{'begin_time':50,'end_time':300,'text':'今完','punctuation':''},
        {'begin_time':350,'end_time':700,'text':'的节奏','punctuation':''}]}]}]}


class BailianTransport:
    def __init__(self,*,pending=False,policy=None,transcript=None):
        self.calls=[]
        self.pending=pending
        self.policy=copy.deepcopy(policy or POLICY)
        self.transcript=copy.deepcopy(transcript or TRANSCRIPT)
        self.submissions=0
        self.after_download=None
        self.task_output=None

    def request(self,url,headers,body=None,**kwargs):
        self.calls.append((url,copy.deepcopy(headers),body,kwargs))
        if '/uploads?' in url:return copy.deepcopy(self.policy),{}
        if url.startswith('https://dashscope-file-'):return None,{}
        if url.endswith('/transcription'):
            self.submissions+=1
            return {'output':{'task_id':'test-task-1','task_status':'PENDING'}},{}
        if '/tasks/' in url:
            if self.task_output is not None:return {'output':self.task_output},{}
            if self.pending:
                self.pending=False
                return {'output':{'task_id':'test-task-1','task_status':'RUNNING'}},{}
            return {'output':{'task_id':'test-task-1','task_status':'SUCCEEDED','results':[{
                'subtask_status':'SUCCEEDED','transcription_url':'https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/test.json?Signature=FAKE_RESULT_SIGNATURE'}]}},{}
        if url.startswith('https://dashscope-result-'):
            if self.after_download:self.after_download()
            return copy.deepcopy(self.transcript),{}
        if url.endswith('/chat/completions'):
            return {'choices':[{'finish_reason':'stop','message':{'content':'连接成功'}}]},{}
        raise AssertionError('Unexpected URL '+url)


class BailianContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory=Path(self.temporary.name).resolve()
        self.audio=self.directory/'真实版本测试.wav'
        with wave.open(str(self.audio),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(bytes(32000))
        self.config=profile('bailian-asr')
        self.plan=run({'action':'plan','config':self.config,'track':inspect_audio(self.audio),
                       'scope':'studio','snapshot':'original','planId':'plan-1'})['plan']
        self.receipt=self.directory/'receipt.json'
        self.request={'action':'recognize','config':self.config,'plan':self.plan,'receipt':str(self.receipt)}

    def test_local_plan_uses_cny_and_50mb_limit_without_network(self):
        self.assertAlmostEqual(self.plan['estimatedCostCNY'],.00044)
        self.assertNotIn('estimatedCostPTC',self.plan)
        self.assertNotIn('maxCostPTC',self.plan)

    def test_one_key_upload_submit_query_and_real_word_timings(self):
        transport=BailianTransport()
        result=run(self.request,KEY,transport=transport)
        self.assertEqual(result['phase'],'review')
        self.assertEqual(result['proposal']['timeline']['quality'],'word')
        self.assertEqual(result['proposal']['timeline']['segments'][0]['words'][1]['startMs'],350)
        policy,upload,submit,query,download=transport.calls
        self.assertEqual(policy[3]['method'],'GET')
        self.assertIn('action=getPolicy&model=fun-asr',policy[0])
        self.assertNotIn('Authorization',upload[1])
        self.assertFalse(upload[3]['expect_json'])
        self.assertIn(b'name="x-oss-object-acl"\r\n\r\nprivate',upload[2])
        self.assertIn(b'name="file";',upload[2])
        self.assertEqual(submit[1]['X-DashScope-Async'],'enable')
        self.assertEqual(submit[1]['X-DashScope-OssResourceResolve'],'enable')
        self.assertEqual(json.loads(submit[2])['parameters'],{'channel_id':[0]})
        self.assertTrue(json.loads(submit[2])['input']['file_urls'][0].startswith('oss://dashscope-instant/'))
        for call in (policy,submit,query):self.assertEqual(call[1]['Authorization'],'Bearer '+KEY)
        self.assertEqual(download[1],{})
        self.assertEqual(query[3]['method'],'GET')
        for path in self.directory.glob('*.json'):self.assertNotIn(KEY,path.read_text())

    def test_pending_task_resumes_without_upload_or_submit_and_ready_is_cached(self):
        first=BailianTransport(pending=True)
        self.assertEqual(run(self.request,KEY,transport=first)['phase'],'polling')
        second=BailianTransport()
        result=run(dict(self.request,action='query'),KEY,transport=second)
        self.assertEqual(result['phase'],'review')
        self.assertEqual(len(second.calls),2)
        self.assertTrue(second.calls[0][0].endswith('/tasks/test-task-1'))
        third=BailianTransport()
        self.assertEqual(run(self.request,KEY,transport=third)['phase'],'review')
        self.assertEqual(third.calls,[])

    def test_unknown_submission_is_not_repeated(self):
        class Timeout(BailianTransport):
            def request(self,url,*args,**kwargs):
                if url.endswith('/transcription'):raise ProviderError('测试提交超时')
                return super().request(url,*args,**kwargs)
        with self.assertRaises(ProviderError):run(self.request,KEY,transport=Timeout())
        transport=BailianTransport()
        result=run(self.request,KEY,transport=transport)
        self.assertEqual(result['phase'],'unknown')
        self.assertEqual(transport.calls,[])

    def test_ready_real_bytes_can_resume_after_metadata_change_without_network(self):
        run(self.request,KEY,transport=BailianTransport())
        receipt_before=self.receipt.read_bytes()
        stat=self.audio.stat()
        os.utime(self.audio,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000))
        transport=BailianTransport()
        result=run(dict(self.request,action='query'),KEY,transport=transport)
        self.assertEqual(result['phase'],'review')
        self.assertEqual(transport.calls,[])
        self.assertEqual(self.receipt.read_bytes(),receipt_before)

    def test_pending_task_can_resume_identical_copy_and_never_resubmit(self):
        run(self.request,KEY,transport=BailianTransport(pending=True),poll_limit=1)
        moved=self.directory/'same-content-new-inode.wav'
        shutil.copyfile(self.audio,moved)
        plan=dict(self.plan,sourcePath=str(moved))
        transport=BailianTransport()
        result=run(dict(self.request,action='query',plan=plan),KEY,transport=transport)
        self.assertEqual(result['phase'],'review')
        self.assertEqual(transport.submissions,0)
        self.assertEqual(len(transport.calls),2)

    def test_resumed_changed_bytes_rejected_even_with_same_size_and_mtime(self):
        run(self.request,KEY,transport=BailianTransport())
        stat=self.audio.stat()
        with self.audio.open('r+b') as stream:
            stream.seek(-2,2);stream.write(b'\x01\x00')
        os.utime(self.audio,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        transport=BailianTransport()
        with self.assertRaises(ValueError):
            run(dict(self.request,action='query'),KEY,transport=transport)
        self.assertEqual(transport.calls,[])

    def test_new_submission_still_requires_original_file_proof(self):
        stat=self.audio.stat()
        os.utime(self.audio,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000))
        transport=BailianTransport()
        with self.assertRaises(ValueError):run(self.request,KEY,transport=transport)
        self.assertEqual(transport.calls,[])

    def test_rejected_quota_request_requires_explicit_reconfirmation(self):
        class QuotaBlocked(BailianTransport):
            def request(self,url,*args,**kwargs):
                if url.endswith('/transcription'):
                    raise ProviderError('免费额度不足',http_status=403,code='AllocationQuota.FreeTierOnly')
                return super().request(url,*args,**kwargs)
        with self.assertRaises(ProviderError):run(self.request,KEY,transport=QuotaBlocked())
        receipt=json.loads(self.receipt.read_text())
        self.assertEqual(receipt['state'],'quota_blocked')
        self.assertNotIn('taskId',receipt)
        query=BailianTransport()
        self.assertEqual(run(dict(self.request,action='query'),KEY,transport=query)['phase'],'upload_required')
        self.assertEqual(query.calls,[])
        confirmed=BailianTransport()
        self.assertEqual(run(self.request,KEY,transport=confirmed)['phase'],'review')
        self.assertEqual(confirmed.submissions,1)

    def test_pre_submit_permission_failure_can_be_reconfirmed_without_duplicate_asr(self):
        class Denied(BailianTransport):
            def request(self,*args,**kwargs):raise ProviderError('密钥无效')
        with self.assertRaises(ProviderError):run(self.request,KEY,transport=Denied())
        self.assertEqual(json.loads(self.receipt.read_text())['state'],'upload_failed')
        query=BailianTransport()
        self.assertEqual(run(dict(self.request,action='query'),KEY,transport=query)['phase'],'upload_required')
        self.assertEqual(query.calls,[])
        confirmed=BailianTransport()
        self.assertEqual(run(self.request,KEY,transport=confirmed)['phase'],'review')
        self.assertEqual(confirmed.submissions,1)

    def test_upload_policy_cannot_send_audio_to_other_hosts_or_public_storage(self):
        for field,value in [('upload_host','https://evil.example'),('upload_host','https://dashscope-file-test.oss-cn-beijing.aliyuncs.com.evil.example'),
                            ('upload_host','http://dashscope-file-test.oss-cn-beijing.aliyuncs.com'),
                            ('x_oss_object_acl','public-read'),('upload_dir','dashscope-instant/../../other')]:
            policy=copy.deepcopy(POLICY);policy['data'][field]=value
            transport=BailianTransport(policy=policy)
            receipt=self.directory/(field+str(len(value))+'.json')
            with self.assertRaises(ProviderError):run(dict(self.request,receipt=str(receipt)),KEY,transport=transport)
            self.assertEqual(len(transport.calls),1)

    def test_result_download_rejects_other_hosts_and_wrong_task_or_failed_subtask(self):
        bad_outputs=[{'task_id':'different','task_status':'RUNNING'},
            {'task_id':'test-task-1','task_status':'SUCCEEDED','results':[{'subtask_status':'FAILED'}]},
            {'task_id':'test-task-1','task_status':'SUCCEEDED','results':[{'subtask_status':'SUCCEEDED','transcription_url':'https://evil.example/result'}]}]
        for index,output in enumerate(bad_outputs):
            transport=BailianTransport();transport.task_output=output
            with self.assertRaises(ProviderError):run(dict(self.request,receipt=str(self.directory/f'bad-{index}.json')),KEY,transport=transport)
            self.assertEqual(len(transport.calls),4)

    def test_sentence_fallback_and_invalid_timing_rejection(self):
        payload=copy.deepcopy(TRANSCRIPT)
        payload['transcripts'][0]['sentences'][0]['text']='今完的节奏。'
        result=speech_result(payload,self.config,self.plan['versionId'],2000)
        self.assertEqual(result['timeline']['quality'],'sentence')
        self.assertNotIn('words',result['timeline']['segments'][0])
        for value in (True,-1,2500):
            bad=copy.deepcopy(TRANSCRIPT);bad['transcripts'][0]['sentences'][0]['begin_time']=value
            with self.assertRaises(ValueError):speech_result(bad,self.config,self.plan['versionId'],2000)

    def test_source_changes_during_cloud_task_never_produce_applicable_result(self):
        transport=BailianTransport()
        transport.after_download=lambda:self.audio.write_bytes(self.audio.read_bytes()+b'changed')
        with self.assertRaises(ValueError):run(self.request,KEY,transport=transport)
        self.assertFalse(self.receipt.with_name('result.json').exists())

    def test_connection_check_verifies_two_capabilities_without_audio(self):
        transport=BailianTransport()
        result=run({'action':'test-bailian','config':profile('qwen'),'speechConfig':self.config},KEY,transport=transport)
        self.assertIn('权限已核验',result['capabilities']['speech'])
        self.assertIn('连接成功',result['capabilities']['text'])
        self.assertEqual(len(transport.calls),2)
        self.assertEqual(transport.submissions,0)
        self.assertNotIn('file',json.loads(transport.calls[1][2]))

    def test_only_official_beijing_host_and_file_models_are_accepted(self):
        workspace='https://workspace-1.cn-beijing.maas.aliyuncs.com'
        self.assertEqual(profile('bailian-asr',workspace,'fun-asr')['baseUrl'],workspace)
        for base in ('https://dashscope.aliyuncs.com:8443','https://dashscope-intl.aliyuncs.com',
                     'https://workspace.cn-beijing.maas.aliyuncs.com.evil.example','https://dashscope.aliyuncs.com/api/v1'):
            with self.assertRaises(ValueError):profile('bailian-asr',base,'fun-asr')
        request=dict(self.request,config=profile('bailian-asr',model='realtime-model'))
        with self.assertRaises(ValueError):run(dict(request,action='plan',track=inspect_audio(self.audio),scope='studio',snapshot='old',planId='new'))


if __name__=='__main__':unittest.main()
