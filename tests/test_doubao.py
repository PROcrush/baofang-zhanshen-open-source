"""Offline contract, timing, and uncertain paid-submit recovery tests."""
import copy
import json
import unittest
from unittest.mock import Mock
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

from app.lyrics import project_lyrics
from worker.doubao import FILE_ORIGIN, parse_timeline, public_audio_url, query_path, submit_request, submitted_id, task_state

VERSION = 'sha256:' + 'a' * 64


def response(words=True):
    row = {'text': '往事随风', 'start_time': 1000, 'end_time': 3000}
    if words:
        row['words'] = [{'text': '往事', 'start_time': 1000, 'end_time': 1800},
                        {'text': '随风', 'start_time': 2400, 'end_time': 3000}]
    return {'code': 0, 'data': {'status': 'SUCCESS', 'result': {'utterances': [row]}}}


class DoubaoContractTests(unittest.TestCase):
    def test_submit_uses_singing_query_and_url_body(self):
        path, body = submit_request('https://file.302.ai/test.m4a')
        parsed = urlsplit(path)
        self.assertEqual(parsed.path, '/doubao/vc/submit')
        query = parse_qs(parsed.query)
        self.assertEqual(query['caption_type'], ['singing'])
        self.assertEqual(query['language'], ['zh'])
        self.assertEqual(query['words_per_line'], ['15'])
        self.assertEqual(query['with_speaker_info'], ['False'])
        self.assertEqual(body, {'url': 'https://' + FILE_ORIGIN + '/test.m4a'})
        self.assertEqual(query_path('task-1'), '/doubao/vc/query?id=task-1&blocking=0')

    def test_rejects_untrusted_audio_url_and_injected_task_id(self):
        for url in ('http://file.302.ai/a', 'https://file.302.ai.evil.test/a',
                    'https://user:secret@file.302.ai/a', 'https://file.302.ai:8443/a',
                    'https://other.oss-cn-shenzhen.aliyuncs.com/a',
                    'https://302ai.oss-cn-shenzhen.aliyuncs.com.evil.test/a',
                    'https://file.302.ai/a b', 'https://file.302.ai/a\nInjected:1',
                    'https://file.302.ai', 'https://file.302.ai/', 'https://@file.302.ai/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                submit_request(url)
        with self.assertRaises(ValueError):
            query_path('task&blocking=1')

    def test_7789_origin_mapping_retains_exact_object_and_signed_query(self):
        suffix = '/gpt/imgs/20261009/a%20b.m4a?OSSAccessKeyId=test&Signature=a%2Bb%3D'
        expected = 'https://' + FILE_ORIGIN + suffix
        for host in ('file.302.ai', 'file.302ai.cn'):
            self.assertEqual(public_audio_url('https://' + host + suffix), expected)
        self.assertEqual(public_audio_url(expected), expected)
        self.assertEqual(public_audio_url(' https://file.302.ai:443' + suffix + ' '), expected)

    def test_task_id_requires_confirmed_submit(self):
        self.assertEqual(submitted_id({'code': 0, 'id': 'task-1'}), 'task-1')
        for value in ({'code': 401, 'id': 'task-1'}, {'code': 0}, {'code': 0, 'id': True},
                      {'id': 'task-1'}, {'code': True, 'id': 'task-1'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                submitted_id(value)

    def test_inner_provider_code_and_id_override_wrapper(self):
        self.assertEqual(submitted_id({'code': 200, 'id': 'wrapper',
                                      'data': {'status_code': 0, 'task_id': 'actual'}}), 'actual')
        for field in ('code', 'status_code', 'err_code', 'errCode'):
            payload = {'code': 0, 'id': 'wrapper', 'error': {field: 1022, 'message': 'download failed'}}
            with self.subTest(field=field), self.assertRaises(ValueError):
                submitted_id(payload)
            self.assertEqual(task_state(payload), 'unknown')

    def test_native_unknown_query_stays_resumable_and_markers_are_processing(self):
        self.assertEqual(task_state({'code': 9876}), 'unknown')
        self.assertEqual(task_state({'code': 9876, 'data': {'status': 'running'}}), 'processing')
        self.assertEqual(task_state({'error': {'err_code': -10006}}), 'quota_exhausted')
        for payload in ({'data': {'queuePosition': 0}}, {'id': 'task-1'}, {'progress': 0},
                        {'updatedAt': '2026-10-09'}):
            with self.subTest(payload=payload):
                self.assertEqual(task_state(payload), 'processing')
        self.assertEqual(task_state({'progress': True}), 'unknown')
        self.assertEqual(task_state({'progress': float('nan')}), 'unknown')

    def test_envelope_success_is_not_completed_task(self):
        self.assertEqual(task_state({'code': 'success', 'data': {'status': 'IN_PROGRESS'}}), 'processing')
        self.assertEqual(task_state({'code': 2000}), 'processing')
        self.assertEqual(task_state({'code': 1002}), 'unknown')
        self.assertEqual(task_state({'code': 0}), 'empty')
        self.assertEqual(task_state({'code': 401}), 'failed')
        self.assertEqual(task_state({'code': 'success', 'data': {'status': 'FAILED'}}), 'failed')
        self.assertEqual(task_state({'data': {'status': 'unexpected'}}), 'unknown')
        self.assertEqual(task_state(response()), 'success')

    def test_millisecond_clock_preserves_token_gaps_and_trim_offset(self):
        timeline = parse_timeline(response(), VERSION, 30000, trim_head_ms=20000)
        row = timeline['segments'][0]
        self.assertEqual((row['startMs'], row['endMs']), (21000, 23000))
        self.assertEqual([w['text'] for w in row['words']], ['往事', '随风'])
        self.assertEqual(len(row['words']), 2)  # no synthetic character anchors
        self.assertEqual(project_lyrics(timeline, 22200)['progress'], .5)
        self.assertFalse(timeline['acousticAccuracyVerified'])

    def test_sentence_only_does_not_invent_word_timestamps(self):
        timeline = parse_timeline(response(False), VERSION, 5000)
        self.assertEqual(timeline['quality'], 'sentence')
        self.assertNotIn('words', timeline['segments'][0])
        self.assertEqual(project_lyrics(timeline, 1800)['progress'], 0)

    def test_word_text_mismatch_keeps_sentence_and_downgrades(self):
        payload = response()
        payload['data']['result']['utterances'][0]['words'][0]['text'] = '往昔'
        timeline = parse_timeline(payload, VERSION, 5000)
        self.assertEqual(timeline['quality'], 'sentence')
        self.assertEqual(timeline['segments'][0]['text'], '往事随风')

    def test_invalid_overlap_nonfinite_or_out_of_source_range_rejected(self):
        for start, end in ((1700, 2500), (float('nan'), 2900), (-1, 2900), (2400, 6000)):
            payload = response()
            payload['data']['result']['utterances'][0]['words'][1].update(start_time=start, end_time=end)
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                parse_timeline(payload, VERSION, 5000)
        with self.assertRaises(ValueError):
            parse_timeline(response(), VERSION, 22000, 20000)

    def test_json_wrapped_result_and_deep_unknown_result(self):
        payload = response()
        self.assertEqual(parse_timeline({'data': json.dumps(payload)}, VERSION, 5000)['quality'], 'word')
        for _ in range(20):
            payload = {'data': payload}
        with self.assertRaises(ValueError):
            task_state(payload)


class PaidSubmitRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import doubao_preflight
        cls.module = doubao_preflight

    def setUp(self):
        self.state = {'stage': 'prepared', 'audio_url': 'https://file.302.ai/test.m4a'}
        self.saved, self.requests = [], []

    def save(self):
        self.saved.append(copy.deepcopy(self.state))

    def request(self, path, body):
        self.requests.append((path, body))
        return {'code': 0, 'id': 'new-doubao-task'}

    def test_submit_persists_intent_before_post_and_known_id_never_resubmits(self):
        def checked_request(path, body):
            self.assertEqual(self.saved[-1]['stage'], 'submit_started_no_retry')
            return self.request(path, body)
        self.module.submit_once(self.state, self.save, checked_request)
        self.module.submit_once(self.state, self.save, checked_request)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.state['task_id'], 'new-doubao-task')
        self.assertEqual(self.state['stage'], 'task_created')

    def test_transport_failure_preserves_uncertain_intent_and_blocks_retry(self):
        def failed(path, body):
            self.requests.append((path, body))
            raise TimeoutError()
        with self.assertRaises(TimeoutError):
            self.module.submit_once(self.state, self.save, failed)
        self.assertEqual(self.state['stage'], 'submit_started_no_retry')
        with self.assertRaises(ValueError):
            self.module.submit_once(self.state, self.save, failed)
        self.assertEqual(len(self.requests), 1)

    def test_missing_task_id_preserves_response_and_blocks_retry(self):
        with self.assertRaises(ValueError):
            self.module.submit_once(self.state, self.save, lambda *_: {'code': 0})
        self.assertEqual(self.state['submit_receipt'], {'code': 0})
        with self.assertRaises(ValueError):
            self.module.submit_once(self.state, self.save, self.request)
        self.assertEqual(self.requests, [])

    def test_save_failure_prevents_network_submit(self):
        def failed_save():
            raise OSError('disk full')
        with self.assertRaises(OSError):
            self.module.submit_once(self.state, failed_save, self.request)
        self.assertEqual(self.requests, [])

    def test_http_error_response_diagnostics_redact_key_and_upload_url(self):
        url, key = 'https://file.302.ai/private.m4a', 'test-secret-key'
        raw = json.dumps({'error': 'upstream failed', 'echo': key, 'url': url}).encode()
        metadata, payload = self.module.decode_response(502, raw, key, url)
        self.assertEqual(metadata['http_status'], 502)
        self.assertEqual(payload['echo'], '[REDACTED]')
        self.assertEqual(payload['url'], '[UPLOAD_URL]')
        html = self.module.decode_response(503, b'<html>unavailable</html>', key, url)
        self.assertIsNone(html[1])
        self.assertIn('unavailable', html[0]['non_json_excerpt'])
        origin = public_audio_url(url)
        metadata, payload = self.module.decode_response(500, json.dumps({'message': origin}).encode(), key, url)
        self.assertEqual(payload['message'], '[UPLOAD_URL]')
        metadata, payload = self.module.decode_response(500,
            json.dumps({'error': {'errCode': 1022, 'message_cn': origin}}).encode(), key, url)
        self.assertEqual(metadata['provider_code'], '1022')
        self.assertEqual(metadata['provider_message'], '[UPLOAD_URL]')

    def test_remote_audio_preflight_uses_real_client_header_and_validates_size(self):
        response = BytesIO(b'\x00')
        response.code = 206
        response.headers = {'Content-Type': 'audio/mp4', 'Content-Range': 'bytes 0-0/4246956'}
        opener = Mock()
        opener.open.return_value = response
        result = self.module.verify_remote_audio(opener, 'https://file.302.ai/a.m4a', 4246956)
        self.assertTrue(result['verified'])
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://' + FILE_ORIGIN + '/a.m4a')
        self.assertEqual(req.get_header('User-agent'), 'BaofangWarrior/0.1')
        self.assertEqual(req.get_header('Range'), 'bytes=0-0')
        self.assertIsNone(req.get_header('Authorization'))

    def test_remote_audio_error_document_or_wrong_size_rejected(self):
        for mime, size in (('text/html', '4246956'), ('audio/mp4', '123')):
            response = BytesIO(b'x')
            response.code = 200
            response.headers = {'Content-Type': mime, 'Content-Length': size}
            opener = Mock()
            opener.open.return_value = response
            with self.subTest(mime=mime, size=size), self.assertRaises(ValueError):
                self.module.verify_remote_audio(opener, 'https://file.302.ai/a.m4a', 4246956)


if __name__ == '__main__':
    unittest.main()
