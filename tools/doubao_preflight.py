"""Reusable Doubao request preflight and uncertain-submit recovery helpers.

These helpers require an explicit caller and never load local media, credentials,
or saved cloud receipts on import. Network access uses the caller's opener.
"""
import json
import urllib.request

from worker.doubao import public_audio_url, provider_code, provider_message, submit_request, submitted_id

USER_AGENT = 'BaofangWarrior/0.1'


def submit_once(state, save, request):
    if state.get('task_id'):
        return
    if state.get('stage') != 'prepared':
        raise ValueError('先前提交结果未确认；保留回执，禁止重复付费提交')
    path, body = submit_request(state['audio_url'])
    state['stage'] = 'submit_started_no_retry'
    save()
    response = request(path, body)
    state['submit_receipt'] = response
    state['stage'] = 'submit_response_received'
    save()
    state['task_id'] = submitted_id(response)
    state['stage'] = 'task_created'
    save()


def decode_response(status, raw, key, audio_url):
    """Redact response diagnostics before they can reach disk or the console."""
    text = raw.decode('utf-8', errors='replace').replace(key, '[REDACTED]')
    for url in (audio_url, public_audio_url(audio_url)):
        text = text.replace(url, '[UPLOAD_URL]')
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError):
        payload = None
    metadata = {'http_status': status, 'response_json': payload,
                'non_json_excerpt': text[:2000] if payload is None else None}
    if payload is not None:
        metadata.update(provider_code=provider_code(payload), provider_message=provider_message(payload))
    return metadata, payload


def verify_remote_audio(opener, url, expected_bytes):
    url = public_audio_url(url)
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Range': 'bytes=0-0'})
    with opener.open(req, timeout=30) as response:
        # Stop after one byte even if a server ignores Range.
        first_byte = response.read(1)
        mime = response.headers.get('Content-Type', '').split(';')[0].lower()
        size = (response.headers.get('Content-Range', '').rsplit('/', 1)[-1]
                if response.code == 206 else response.headers.get('Content-Length'))
        if response.code not in (200, 206) or not first_byte or size != str(expected_bytes) or mime not in ('audio/mp4', 'audio/x-m4a', 'application/octet-stream'):
            raise ValueError('已有上传音频未通过访问/大小校验，不创建付费任务')
        return {'http_status': response.code, 'expected_bytes': expected_bytes,
                'content_type': mime, 'verified': True,
                'download_host': '302ai.oss-cn-shenzhen.aliyuncs.com'}
