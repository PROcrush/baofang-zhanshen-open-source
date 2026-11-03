"""Offline only. Every network-capable job gets an injected fake transport."""
import contextlib
import fcntl
import io
import json
import os
import socket
import stat
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from worker import cloud_pipeline as pipeline


KEY = 'offline-fixture-key'
QUEUED = {'code': 2000, 'status': 'queued'}
SUCCESS = {'code': 0, 'utterances': [{'text': '你好', 'start_time': 100,
    'end_time': 700, 'words': [{'text': '你', 'start_time': 100, 'end_time': 300},
                             {'text': '好', 'start_time': 350, 'end_time': 700}]}]}


class FakeTransport:
    def __init__(self, *, upload=None, submitted=None, queries=None):
        self.upload_result = upload if upload is not None else {'code': 200, 'data': 'https://file.302.ai/object.wav'}
        self.submit_result = submitted if submitted is not None else {'code': 0, 'id': 'offline-task'}
        self.queries = list(queries if queries is not None else [QUEUED])
        self.calls, self.hook = [], None

    def dispatch(self, stage, host, path, body, key, result):
        self.calls.append((stage, host, path, body, key))
        if self.hook:
            self.hook(stage)
        if isinstance(result, Exception):
            raise result
        return result

    def upload(self, host, source_path, key):
        return self.dispatch('upload', host, source_path, None, key, self.upload_result)

    def submit(self, host, path, body, key):
        return self.dispatch('submit', host, path, body, key, self.submit_result)

    def query(self, host, path, key):
        return self.dispatch('query', host, path, None, key, self.queries.pop(0))


class CloudPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.audio = self.root / 'selected.wav'
        with wave.open(str(self.audio), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(1000)
            handle.writeframes(b'\0\0' * 2000)
        self.receipt = self.root / 'job' / 'receipt.json'
        # Prevent all network, including an accidentally omitted fake transport.
        self.socket_guard = patch.object(socket, 'create_connection', side_effect=AssertionError('network forbidden'))
        self.socket_guard.start()
        self.duration_backend = patch.object(pipeline, '_ffprobe', return_value=None)
        self.duration_backend.start()
        self.plan = pipeline.preflight({'path': str(self.audio)}, 'api.302ai.cn', 1)
        self.plan.update(planId='offline-plan', intentId='offline-intent')

    def tearDown(self):
        self.duration_backend.stop()
        self.socket_guard.stop()
        self.temporary.cleanup()

    def job(self, transport, action='start', plan=None, **kwargs):
        return pipeline.run_job(self.plan if plan is None else plan, self.receipt,
                                action=action, key=kwargs.pop('key', KEY),
                                transport=transport, **kwargs)

    def saved(self):
        return json.loads(self.receipt.read_text())

    def test_preflight_zero_budget_keeps_reviewable_plan(self):
        value = pipeline.preflight({'path': str(self.audio)}, 'https://api.302.ai', 0)
        self.assertEqual(value['durationMs'], 2000)
        self.assertEqual(value['estimatedCostPTC'], .011)
        self.assertFalse(value['budgetAllowed'])
        self.assertTrue(value['uploadAllowed'])
        self.assertTrue(value['estimateOnly'])
        self.assertEqual(value['uploadSource'], str(self.audio))
        self.assertEqual(value['identityProof']['size'], self.audio.stat().st_size)
        self.assertRegex(value['versionId'], r'^sha256:[0-9a-f]{64}$')

    def test_duration_and_conservative_minute_rounding(self):
        self.assertEqual(pipeline._estimate(60000), .011)
        self.assertEqual(pipeline._estimate(60000.001), .021)
        self.assertEqual(pipeline._estimate(120001), .031)
        with patch.object(pipeline, '_duration_info', return_value=(60000.001, 'fixture', 'file_metadata')):
            value = pipeline.preflight({'path': str(self.audio)}, 'api.302.ai', .02)
        self.assertFalse(value['budgetAllowed'])
        self.assertEqual(value['estimatedCostPTC'], .021)

    def test_oversized_audio_is_planned_but_not_uploadable(self):
        with patch.object(pipeline, 'UPLOAD_LIMIT_BYTES', 1):
            value = pipeline.preflight({'path': str(self.audio)}, 'api.302.ai', 1)
        self.assertFalse(value['uploadAllowed'])
        self.assertEqual(value['durationMs'], 2000)

    def test_host_and_version_rejected_without_network(self):
        for host in ('http://api.302.ai', 'https://evil.example', 'https://key@api.302.ai',
                     'https://api.302.ai/path', 'https://api.302.ai?key=secret', 'api.302.ai:444'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                pipeline.preflight({'path': str(self.audio)}, host, 1)
        with self.assertRaises(ValueError):
            pipeline.preflight({'path': str(self.audio), 'versionId': 'sha256:' + 'f' * 64}, 'api.302.ai', 1)

    def test_ffprobe_uses_timeout_and_valid_audio_stream(self):
        fake = subprocess.CompletedProcess([], 0, b'{"streams":[{"codec_type":"audio"}],"format":{"duration":"1.25"}}', b'')
        with patch.object(pipeline, '_ffprobe', return_value='/fixture/ffprobe'), patch.object(pipeline.subprocess, 'run', return_value=fake) as run:
            self.assertEqual(pipeline._duration_ms(self.audio), 1250)
        self.assertEqual(run.call_args.kwargs['timeout'], 15)
        self.assertFalse(run.call_args.kwargs['check'])

    def test_nonwav_invalid_metadata_is_precisely_unavailable(self):
        source = self.root / 'unknown.mp3'
        source.write_bytes(b'fixture')
        with self.assertRaisesRegex(ValueError, 'TinyTag'):
            pipeline.preflight({'path': str(source)}, 'api.302.ai', 0)

    def test_tinytag_fallback_labels_file_metadata_and_reads_no_tags(self):
        source = self.root / 'fixture.mp3'
        source.write_bytes(b'fixture')
        class Tag:
            duration = 65.5
        with patch('tinytag.TinyTag.get', return_value=Tag()) as get:
            result = pipeline.preflight({'path': str(source)}, 'api.302.ai', 0)
        self.assertEqual(result['durationMs'], 65500)
        self.assertEqual(result['durationMethod'], 'tinytag')
        self.assertEqual(result['durationBasis'], 'file_metadata')
        self.assertEqual(result['estimatedCostPTC'], .021)
        self.assertEqual(get.call_args.kwargs, {'tags': False, 'image': False})
        self.assertFalse(result['budgetAllowed'])

    def test_tinytag_invalid_duration_is_rejected(self):
        source = self.root / 'fixture.mp3'
        source.write_bytes(b'fixture')
        for duration in (None, 0, -1, float('nan'), float('inf')):
            with self.subTest(duration=duration), patch('tinytag.TinyTag.get') as get:
                get.return_value.duration = duration
                with self.assertRaisesRegex(ValueError, 'TinyTag'):
                    pipeline.preflight({'path': str(source)}, 'api.302.ai', 1)

    def test_preflight_cli_has_no_task_key_dependency(self):
        input_path = self.root / 'input.json'
        input_path.write_text(json.dumps({'track': {'path': str(self.audio)}, 'host': 'api.302.ai',
                                          'maxCostPTC': 0, 'planId': 'review-plan'}))
        output = io.StringIO()
        original_get = pipeline.os.environ.get
        def prohibit_key(name, default=None):
            if 'KEY' in name:
                raise AssertionError('credential environment access forbidden')
            return original_get(name, default)
        with contextlib.redirect_stdout(output), patch.object(pipeline.os.environ, 'get', side_effect=prohibit_key):
            code = pipeline.main(['--action', 'preflight', '--plan', str(input_path), '--receipt', str(self.receipt)])
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual(rows[-1]['phase'], 'planned')
        self.assertEqual(rows[-1]['plan']['planId'], 'review-plan')
        self.assertFalse(self.receipt.exists())

    def test_intent_precedes_upload_and_submit_and_lock_excludes_parallel(self):
        transport = FakeTransport()
        def verify(stage):
            receipt = self.saved()
            self.assertTrue(receipt['networkStarted'])
            self.assertEqual(receipt['state'], 'unknown' if stage != 'query' else 'polling')
            self.assertEqual(receipt['stage'], stage + '_started')
            second = self.job(FakeTransport())
            self.assertEqual(second['phase'], 'failed')
            self.assertIn('锁', second['detail'])
        transport.hook = verify
        result = self.job(transport)
        self.assertEqual(result['phase'], 'polling')
        self.assertEqual([row[0] for row in transport.calls], ['upload', 'submit', 'query'])
        self.assertEqual(transport.calls[1][3], {'url': 'https://302ai.oss-cn-shenzhen.aliyuncs.com/object.wav'})
        self.assertIn('caption_type=singing', transport.calls[1][2])
        self.assertEqual(self.saved()['reservedCostPTC'], .011)

    def test_queued_restart_query_does_not_upload_or_submit(self):
        first = FakeTransport()
        self.assertEqual(self.job(first)['phase'], 'polling')
        second = FakeTransport(queries=[SUCCESS])
        result = self.job(second, action='query')
        self.assertEqual([row[0] for row in second.calls], ['query'])
        self.assertEqual(result['phase'], 'ready')
        self.assertEqual(result['taskId'], 'offline-task')
        timeline = json.loads(Path(result['timelinePath']).read_text())
        self.assertEqual(timeline['quality'], 'word')
        self.assertEqual(timeline['segments'][0]['words'][1]['startMs'], 350)
        self.assertFalse(timeline['acousticAccuracyVerified'])
        self.assertEqual(timeline['versionId'], self.plan['versionId'])
        self.assertEqual(self.saved()['queryCount'], 2)

    def test_ready_result_reuses_saved_timeline(self):
        ready = self.job(FakeTransport(queries=[SUCCESS]))
        transport = FakeTransport()
        again = self.job(transport, action='query')
        self.assertEqual(again['timelinePath'], ready['timelinePath'])
        self.assertEqual(transport.calls, [])

    def test_new_start_refused_after_known_task(self):
        self.job(FakeTransport())
        before = self.receipt.read_bytes()
        transport = FakeTransport()
        result = self.job(transport)
        self.assertEqual(result['taskId'], 'offline-task')
        self.assertEqual(transport.calls, [])
        self.assertEqual(before, self.receipt.read_bytes())

    def test_missing_id_is_unknown_and_cannot_restart_or_query(self):
        transport = FakeTransport(submitted={'code': 0})
        self.assertEqual(self.job(transport)['phase'], 'unknown')
        self.assertEqual([row[0] for row in transport.calls], ['upload', 'submit'])
        for action in ('start', 'query'):
            transport = FakeTransport()
            self.assertEqual(self.job(transport, action=action)['phase'], 'unknown')
            self.assertEqual(transport.calls, [])

    def test_unclear_success_code_retains_valid_id_for_query_only(self):
        transport = FakeTransport(submitted={'code': 1022, 'id': 'unclear-id'})
        self.assertEqual(self.job(transport)['phase'], 'unknown')
        self.assertEqual(self.saved()['taskId'], 'unclear-id')
        next_transport = FakeTransport(queries=[QUEUED])
        result = self.job(next_transport, action='query')
        self.assertEqual(result['phase'], 'polling')
        self.assertEqual([row[0] for row in next_transport.calls], ['query'])

    def test_http_upload_failure_is_durable_unknown(self):
        failure = pipeline.TransportError('secret ' + KEY, payload={'error': 'https://signed.example/' + KEY}, status=500)
        transport = FakeTransport(upload=failure)
        result = self.job(transport)
        self.assertEqual(result['phase'], 'unknown')
        self.assertEqual([row[0] for row in transport.calls], ['upload'])
        self.assertNotIn(KEY, json.dumps(result))
        self.assertNotIn('https://', result['detail'])
        self.assertEqual(len(self.saved()['rawResponses']), 1)
        self.assertNotIn(KEY, Path(self.saved()['rawResponses'][0]).read_text())
        second = FakeTransport()
        self.job(second)
        self.assertEqual(second.calls, [])

    def test_http_submit_failure_is_not_retried(self):
        transport = FakeTransport(submitted=pipeline.TransportError(payload={'code': 500}))
        self.assertEqual(self.job(transport)['phase'], 'unknown')
        self.assertEqual([row[0] for row in transport.calls], ['upload', 'submit'])
        self.assertEqual(self.saved()['stage'], 'submit_started')
        self.assertTrue(self.saved()['networkStarted'])

    def test_query_http_failure_preserves_id_and_can_resume(self):
        first = FakeTransport(queries=[pipeline.TransportError(payload={'code': 503})])
        result = self.job(first)
        self.assertEqual(result['phase'], 'unknown')
        self.assertEqual(result['taskId'], 'offline-task')
        second = FakeTransport(queries=[SUCCESS])
        self.assertEqual(self.job(second, action='query')['phase'], 'ready')
        self.assertEqual([row[0] for row in second.calls], ['query'])

    def test_budget_or_key_failure_saves_intent_without_network(self):
        for modification, key in (({'maxCostPTC': 0}, KEY), ({'sessionRemainingPTC': 0}, KEY), ({}, '')):
            with self.subTest(modification=modification, key=bool(key)):
                if self.receipt.exists():
                    self.receipt.unlink()
                transport = FakeTransport()
                result = self.job(transport, plan=dict(self.plan, **modification), key=key)
                self.assertEqual(result['phase'], 'failed')
                self.assertEqual(transport.calls, [])
                self.assertFalse(self.saved()['networkStarted'])
                self.assertEqual(self.saved()['stage'], 'intent_saved')

    def test_changed_source_before_upload_fails_without_network(self):
        with self.audio.open('ab') as handle:
            handle.write(b'changed')
        transport = FakeTransport()
        self.assertEqual(self.job(transport)['phase'], 'failed')
        self.assertFalse(self.saved()['networkStarted'])
        self.assertEqual(transport.calls, [])

    def test_changed_source_during_upload_blocks_submit(self):
        transport = FakeTransport()
        def mutate(stage):
            if stage == 'upload':
                with self.audio.open('ab') as handle:
                    handle.write(b'changed')
        transport.hook = mutate
        self.assertEqual(self.job(transport)['phase'], 'unknown')
        self.assertEqual([row[0] for row in transport.calls], ['upload'])
        self.assertEqual(self.saved()['stage'], 'upload_started')

    def test_wrong_route_on_query_never_changes_existing_receipt(self):
        self.job(FakeTransport())
        before = self.receipt.read_bytes()
        transport = FakeTransport()
        result = self.job(transport, action='query', plan=dict(self.plan, host='https://api.302.ai'))
        self.assertEqual(result['phase'], 'failed')
        self.assertEqual(transport.calls, [])
        self.assertEqual(before, self.receipt.read_bytes())

    def test_foreign_or_historical_receipts_are_not_opened_as_jobs(self):
        self.receipt.parent.mkdir()
        self.receipt.write_text('{"legacy":true}')
        before = self.receipt.read_bytes()
        transport = FakeTransport()
        self.assertEqual(self.job(transport, action='query')['phase'], 'failed')
        self.assertEqual(before, self.receipt.read_bytes())
        historical = self.root / 'local-test' / 'receipt.json'
        result = pipeline.run_job(self.plan, historical, action='query', key=KEY, transport=transport)
        self.assertEqual(result['phase'], 'failed')
        self.assertFalse(historical.parent.exists())
        self.assertEqual(transport.calls, [])

    def test_invalid_result_preserves_existing_lyrics(self):
        old = self.root / 'existing-lyrics.json'
        old.write_text('original lyrics')
        invalid = {'code': 0, 'utterances': [{'text': '错', 'start_time': 100, 'end_time': 999999}]}
        result = self.job(FakeTransport(queries=[invalid]))
        self.assertEqual(result['phase'], 'unknown')
        self.assertEqual(old.read_text(), 'original lyrics')
        self.assertNotIn('timelinePath', result)
        self.assertTrue(self.saved()['taskId'])

    def test_provider_failed_or_empty_is_failed_with_id_preserved(self):
        for payload in ({'code': 401}, {'code': 0}, {'code': -10006}):
            with self.subTest(payload=payload):
                if self.receipt.exists():
                    self.receipt.unlink()
                result = self.job(FakeTransport(queries=[payload]))
                self.assertEqual(result['phase'], 'failed')
                self.assertEqual(result['taskId'], 'offline-task')
                self.assertEqual(self.saved()['state'], 'failed')

    def test_private_receipt_raw_timeline_and_no_credentials_in_progress(self):
        notifications = []
        result = self.job(FakeTransport(queries=[SUCCESS]), notify=notifications.append)
        for path in self.receipt.parent.glob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn(KEY, path.read_text())
        self.assertNotIn(KEY, json.dumps(notifications))
        self.assertNotIn('https://', json.dumps(notifications))
        self.assertNotIn('https://', json.dumps(result))

    def test_poll_limit_is_validated_before_network(self):
        transport = FakeTransport()
        self.assertEqual(self.job(transport, poll_limit=100)['phase'], 'failed')
        self.assertEqual(transport.calls, [])
        self.assertFalse(self.receipt.exists())

    def test_unqueried_known_task_stays_durable_polling(self):
        transport = FakeTransport()
        self.assertEqual(self.job(transport, poll_limit=0)['phase'], 'polling')
        self.assertEqual([row[0] for row in transport.calls], ['upload', 'submit'])
        self.assertEqual(self.saved()['queryCount'], 0)

    def test_http_transport_multipart_field_and_no_redirect_retry(self):
        class Response:
            status = 200
            def read(self, limit):
                return b'{"code":200,"data":"https://file.302.ai/object.wav"}'
        class Connection:
            def __init__(self):
                self.request, self.headers, self.sent = None, {}, []
            def putrequest(self, method, path):
                self.request = (method, path)
            def putheader(self, key, value):
                self.headers[key] = value
            def endheaders(self):
                pass
            def send(self, body):
                self.sent.append(body)
            def getresponse(self):
                return Response()
            def close(self):
                pass
        connection = Connection()
        transport = pipeline.HTTPTransport()
        with patch.object(transport, '_connection', return_value=connection):
            result = transport.upload('https://api.302.ai', str(self.audio), KEY)
        self.assertEqual(connection.request, ('POST', '/302/upload-file'))
        body = b''.join(connection.sent)
        self.assertIn(b'name="file"', body)
        self.assertIn(b'filename="audio.wav"', body)
        self.assertEqual(int(connection.headers['Content-Length']), len(body))
        self.assertEqual(result['code'], 200)


if __name__ == '__main__':
    unittest.main()
