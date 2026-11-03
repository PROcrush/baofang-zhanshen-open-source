"""Live acceptance must require explicit network/new-task permission and redact."""
import json
import io
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from tools.check_bailian_live import AuditTransport, main
from worker.ai_providers import profile


class LiveAuditTests(unittest.TestCase):
    def test_new_real_file_requires_execute_and_sufficient_explicit_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            audio = runtime / 'fixture.wav'
            with wave.open(str(audio), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(bytes(32000))
            for model, flags in [('fun-asr', []), ('fun-asr', ['--execute']),
                                 ('fun-asr', ['--execute', '--max-new-cost-cny', '.0001']),
                                 ('fun-asr-mtl', ['--execute', '--max-new-cost-cny', '1'])]:
                with self.subTest(model=model, flags=flags):
                    config = profile('bailian-asr', model=model)
                    (runtime / 'ai-profiles.json').write_text(json.dumps({'profiles': {'bailian-asr': config}}))
                    output = io.StringIO()
                    argv = ['check_bailian_live', '--runtime', str(runtime), str(audio), *flags]
                    with patch('sys.argv', argv), redirect_stdout(output), \
                         patch('tools.check_bailian_live.KeychainStore') as keychain, \
                         patch.object(AuditTransport, 'request') as network:
                        main()
                    self.assertEqual(json.loads(output.getvalue())['phase'], 'preflight-only')
                    keychain.assert_not_called()
                    network.assert_not_called()
                    self.assertFalse((runtime / 'bailian-jobs').exists())

    def test_network_disabled_by_default_and_resume_cannot_upload_or_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            audit=AuditTransport(Path(directory)/'audit.jsonl')
            audit.transport=Mock()
            with self.assertRaises(ValueError):
                audit.request('https://dashscope.aliyuncs.com/api/v1/tasks/original-id',{},method='GET')
            audit.execute=True
            for url in ('https://dashscope.aliyuncs.com/api/v1/uploads?action=getPolicy',
                        'https://dashscope-file-test.oss-cn-beijing.aliyuncs.com',
                        'https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription'):
                with self.assertRaises(ValueError):audit.request(url,{})
            audit.transport.request.assert_not_called()

    def test_audit_keeps_task_usage_but_never_credentials_signed_urls_or_bodies(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'audit.jsonl'
            audit=AuditTransport(path,execute=True)
            payload={'output':{'task_id':'original-id','task_status':'SUCCEEDED',
                               'results':[{'transcription_url':'https://example.invalid/?Signature=FAKE_SIGNATURE'}]},
                     'request_id':'request-id','usage':{'duration':408,'unexpected':'FAKE_SECRET'}}
            audit.transport=Mock()
            audit.transport.request.return_value=(payload,{'secret':'FAKE_SECRET'})
            result,_=audit.request('https://dashscope.aliyuncs.com/api/v1/tasks/original-id',
                                   {'Authorization':'Bearer FAKE_SECRET'},b'PRIVATE_AUDIO',method='GET')
            self.assertEqual(result,payload)
            text=path.read_text()
            for secret in ('FAKE_SECRET','FAKE_SIGNATURE','PRIVATE_AUDIO','Authorization','transcription_url'):
                self.assertNotIn(secret,text)
            events=[json.loads(line) for line in text.splitlines()]
            self.assertEqual(events[-1]['task_id'],'original-id')
            self.assertEqual(events[-1]['usage'],{'duration':408})
