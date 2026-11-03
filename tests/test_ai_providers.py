import copy
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app.storage import inspect_audio
from worker.ai_providers import (HTTPTransport, ProviderError, chat_request, correct_lines, credential,
                                profile, speech_result, transcribe)
from worker.ai_tasks import run

KEY='TEST_ONLY_FAKE_KEY_NOT_SENT'
VERSION='sha256:'+'a'*64


class Transport:
    def __init__(self,payload,headers=None):
        self.payload=payload
        self.headers=headers or {}
        self.calls=[]

    def request(self,url,headers,body,**kwargs):
        self.calls.append((url,headers,body,kwargs))
        return copy.deepcopy(self.payload),self.headers


def chat(lines,reason='stop'):
    return {'choices':[{'finish_reason':reason,'message':{'content':json.dumps({'lines':lines},ensure_ascii=False)}}]}


class AIContractsTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{'startMs':100,'endMs':900,'text':'今完的节奏','characters':[{'startMs':100,'endMs':900,'text':'今完的节奏'}]},
                   {'startMs':1000,'endMs':1900,'text':'让灯光展开'}]

    def test_domestic_provider_paths_and_parameters(self):
        for provider,extra in [('qwen',{'enable_thinking':False}),('deepseek',{'thinking':{'type':'disabled'}}),('doubao',{'thinking':{'type':'disabled'}}),('glm',{'thinking':{'type':'disabled'}}),('siliconflow',{'enable_thinking':False})]:
            config=profile(provider)
            url,headers,data=chat_request(config,KEY,[{'role':'user','content':'测试'}])
            self.assertTrue(url.endswith('/chat/completions'))
            self.assertEqual(headers['Authorization'],'Bearer '+KEY)
            body=json.loads(data)
            for field,value in extra.items():self.assertEqual(body[field],value)
            self.assertFalse(body['stream'])
            self.assertNotIn(KEY,data.decode())
            self.assertIn('max_completion_tokens' if provider=='doubao' else 'max_tokens',body)
        self.assertEqual(profile('deepseek')['model'],'deepseek-flash')

    def test_pasted_endpoint_is_normalized_without_duplicate_v1(self):
        config=profile('qwen','https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions','qwen-plus')
        self.assertEqual(config['baseUrl'],'https://dashscope.aliyuncs.com/compatible-mode/v1')
        self.assertEqual(profile('qwen','https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1','qwen-plus')['provider'],'qwen')

    def test_provider_keys_cannot_be_silently_routed_to_another_host(self):
        for endpoint in ('https://api.deepseek.com@evil.example/v1','https://evil.example/v1','https://api.deepseek.com?key=secret','http://api.deepseek.com'):
            with self.assertRaises(ValueError):profile('deepseek',endpoint,'deepseek-flash')
        self.assertEqual(profile('compatible-text','http://127.0.0.1:11434/v1','local-model')['baseUrl'],'http://127.0.0.1:11434/v1')
        with self.assertRaises(ValueError):profile('compatible-text','http://remote.example/v1','model')
        with self.assertRaises(ValueError):credential('bad\nkey')

    def test_correction_preserves_times_and_does_not_expose_them_to_model(self):
        transport=Transport(chat([{'index':0,'text':'今晚的节奏'},{'index':1,'text':'让灯光展开'}]))
        original=copy.deepcopy(self.rows)
        result=correct_lines(profile('qwen'),KEY,self.rows,transport)
        self.assertEqual(self.rows,original)
        self.assertEqual(result['rows'][0]['startMs'],100)
        self.assertEqual(result['rows'][0]['endMs'],900)
        self.assertNotIn('characters',result['rows'][0])
        body=json.loads(transport.calls[0][2])
        self.assertNotIn('startMs',body['messages'][1]['content'])
        self.assertEqual(len(result['changes']),1)
        self.assertFalse(result['timingRealigned'])

    def test_malformed_model_suggestions_cannot_change_rows(self):
        cases=[[{'index':0,'text':'新增的一句'}], [{'index':1,'text':'今晚的节奏'},{'index':0,'text':'让灯光展开'}],
               [{'index':0,'text':''},{'index':1,'text':'让灯光展开'}], [{'index':True,'text':'今晚的节奏'},{'index':1,'text':'让灯光展开'}]]
        for lines in cases:
            with self.assertRaises(ValueError):correct_lines(profile('qwen'),KEY,self.rows,Transport(chat(lines)))
        with self.assertRaises(ValueError):correct_lines(profile('qwen'),KEY,self.rows,Transport(chat([{'index':0,'text':'a'},{'index':1,'text':'b'}],reason='length')))

    def test_plain_text_asr_never_fabricates_timings(self):
        result=speech_result({'text':'今晚的节奏'},profile('siliconflow-asr'),VERSION,2000)
        self.assertIsNone(result['timeline'])
        self.assertFalse(result['hasTimings'])

    def test_volc_real_word_intervals_and_sentence_fallback(self):
        payload={'result':{'text':'灯光','utterances':[{'text':'灯光','start_time':100,'end_time':900,'words':[{'text':'灯','start_time':100,'end_time':400},{'text':'光','start_time':500,'end_time':900}]}]}}
        result=speech_result(payload,profile('volc-asr'),VERSION,2000)
        self.assertEqual(result['timeline']['quality'],'word')
        self.assertEqual(result['timeline']['segments'][0]['words'][1]['startMs'],500)
        payload['result']['utterances'][0]['text']='灯光。'
        result=speech_result(payload,profile('volc-asr'),VERSION,2000)
        self.assertEqual(result['timeline']['quality'],'sentence')
        self.assertNotIn('words',result['timeline']['segments'][0])

    def test_out_of_range_or_overlapping_service_time_is_rejected(self):
        config=profile('compatible-asr','https://example.com/v1','speech')
        for rows in ([{'start':0,'end':3,'text':'越界'}],[{'start':0,'end':1,'text':'一句'},{'start':0.5,'end':1.2,'text':'重叠'}],[{'start':True,'end':1,'text':'错误'}]):
            with self.assertRaises(ValueError):speech_result({'text':'歌词','segments':rows},config,VERSION,2000)

    def test_volc_headers_base64_and_status_are_real_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            audio=Path(directory)/'音频.wav'
            audio.write_bytes(b'fixture-audio')
            transport=Transport({'result':{'text':'测试','utterances':[{'text':'测试','start_time':10,'end_time':100}]}},{'x-api-status-code':'20000000'})
            result=transcribe(profile('volc-asr'),KEY,audio,VERSION,1000,transport)
            url,headers,body,_=transport.calls[0]
            self.assertTrue(url.endswith('/api/v3/auc/bigmodel/recognize/flash'))
            self.assertEqual(headers['X-Api-Key'],KEY)
            self.assertEqual(headers['X-Api-Resource-Id'],'volc.bigasr.auc_turbo')
            self.assertEqual(headers['X-Api-Sequence'],'-1')
            self.assertTrue(json.loads(body)['audio']['data'])
            self.assertTrue(result['hasTimings'])
            with self.assertRaises(ProviderError):transcribe(profile('volc-asr'),KEY,audio,VERSION,1000,Transport({'result':{}},{'x-api-status-code':'45000001'}))

    def test_worker_preflight_and_single_plan_submission_are_bound_to_file(self):
        with tempfile.TemporaryDirectory() as directory:
            audio=Path(directory)/'歌.wav'
            with wave.open(str(audio),'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(bytes(16000))
            track=inspect_audio(audio)
            config=profile('volc-asr')
            planned=run({'action':'plan','config':config,'track':track,'scope':'studio','snapshot':'digest','planId':'test-plan'})['plan']
            transport=Transport({'result':{'text':'测试','utterances':[{'text':'测试','start_time':10,'end_time':100}]}},{'x-api-status-code':'20000000'})
            request={'action':'recognize','config':config,'plan':planned,'receipt':str(Path(directory)/'receipt.json')}
            result=run(request,KEY,transport=transport)
            self.assertEqual(result['proposal']['timeline']['versionId'],track['versionId'])
            with self.assertRaises(ValueError):run(request,KEY,transport=transport)
            self.assertEqual(len(transport.calls),1)
            self.assertNotIn(KEY,Path(request['receipt']).read_text())

    def test_302_existing_contract_is_reused_and_query_does_not_resubmit(self):
        from test_cloud_pipeline import FakeTransport,SUCCESS,QUEUED
        with tempfile.TemporaryDirectory() as directory:
            audio=Path(directory)/'302.wav'
            with wave.open(str(audio),'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(bytes(16000))
            config=profile('302-singing',budget=0.1)
            plan=run({'action':'plan','config':config,'track':inspect_audio(audio),'scope':'studio','snapshot':'original','planId':'302-plan'})['plan']
            request={'action':'recognize','config':config,'plan':plan,'receipt':str(Path(directory).resolve()/'receipt.json')}
            first=FakeTransport(queries=[QUEUED])
            result=run(request,KEY,transport=first)
            self.assertEqual(result['phase'],'polling',result['detail'])
            second=FakeTransport(queries=[SUCCESS])
            result=run(dict(request,action='query'),KEY,transport=second)
            self.assertEqual(result['proposal']['timeline']['segments'][0]['words'][0]['startMs'],100)
            self.assertEqual([row[0] for row in second.calls],['query'])

    def test_http_auth_and_rate_errors_never_echo_service_secrets(self):
        class Response:
            def __init__(self,status):self.status=status
            def read(self,_):return json.dumps({'error':KEY}).encode()
            def getheaders(self):return []
        class Connection:
            def __init__(self,status):self.status=status
            def request(self,*args,**kwargs):pass
            def getresponse(self):return Response(self.status)
            def close(self):pass
        for status in (401,403,429,302,500):
            with patch('worker.ai_providers.http.client.HTTPSConnection',return_value=Connection(status)):
                with self.assertRaises(ProviderError) as error:
                    HTTPTransport().request('https://api.deepseek.com/chat/completions',{'Authorization':'Bearer '+KEY},b'{}')
                self.assertNotIn(KEY,str(error.exception))

    def test_bailian_free_quota_error_is_distinct_from_invalid_key(self):
        class Response:
            status=403
            def __init__(self,payload):self.payload=payload
            def read(self,_):return json.dumps(self.payload).encode()
            def getheaders(self):return []
        class Connection:
            def __init__(self,payload):self.payload=payload
            def request(self,*args,**kwargs):pass
            def getresponse(self):return Response(self.payload)
            def close(self):pass
        error={'code':'AllocationQuota.FreeTierOnly','message':KEY}
        for host,payload in [('dashscope.aliyuncs.com',{'error':error}),
                             ('test-workspace.cn-beijing.maas.aliyuncs.com',error)]:
            with self.subTest(host=host),patch('worker.ai_providers.http.client.HTTPSConnection',return_value=Connection(payload)):
                with self.assertRaises(ProviderError) as result:
                    HTTPTransport().request('https://'+host+'/compatible-mode/v1/chat/completions',{'Authorization':'Bearer '+KEY},b'{}')
                self.assertIn('免费额度',str(result.exception))
                self.assertNotIn('密钥无效',str(result.exception))
                self.assertNotIn(KEY,str(result.exception))
        for host,payload in [('api.deepseek.com',{'error':error}),
                             ('dashscope.aliyuncs.com',{'error':KEY})]:
            with self.subTest(host=host,payloadType=type(payload['error']).__name__),patch('worker.ai_providers.http.client.HTTPSConnection',return_value=Connection(payload)):
                with self.assertRaises(ProviderError) as result:
                    HTTPTransport().request('https://'+host+'/chat/completions',{'Authorization':'Bearer '+KEY},b'{}')
                self.assertIn('密钥无效',str(result.exception))
                self.assertNotIn(KEY,str(result.exception))


if __name__=='__main__':unittest.main()
