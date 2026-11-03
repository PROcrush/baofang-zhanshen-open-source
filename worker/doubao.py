"""302 Doubao singing contract and source-millisecond lyrics; no I/O at import."""
import json
import math
import re
from urllib.parse import urlencode, urlsplit
from app.storage import validate_timeline

PROVIDER = '302-doubao-vc'
HOSTS = {'api.302.ai', 'api.302ai.cn'}
FILE_ORIGIN = '302ai.oss-cn-shenzhen.aliyuncs.com'
FILE_HOSTS = {'file.302.ai', 'file.302ai.cn', FILE_ORIGIN}
PARAMETERS = {'words_per_line': 15, 'max_lines': 1, 'use_itn': 'False',
              'language': 'zh', 'caption_type': 'singing', 'use_punc': 'False',
              'use_ddc': 'False', 'with_speaker_info': 'False'}
SUCCESS = {'0', '1000', '200', 'success', 'ok', 'succeeded'}
PROCESSING = {'in-progress', 'queue', 'in_progress', 'running', 'created',
              'pending', 'queued', 'processing'}
FAILED = {'failed', 'error', 'failure', 'cancelled', 'canceled'}
ENVELOPES = ('error', 'output', 'result', 'data', 'resp')


def public_audio_url(audio_url):
    """Apply the supplied 7789 BYOK patch; retain object path and signed query."""
    if not isinstance(audio_url, str):
        raise ValueError('上传地址必须是字符串')
    audio_url = audio_url.strip()
    parsed = urlsplit(audio_url)
    if (parsed.scheme != 'https' or parsed.hostname not in FILE_HOSTS
            or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
            or not parsed.path.startswith('/') or parsed.path == '/'
            or parsed.fragment or any(char.isspace() or ord(char) < 32 for char in audio_url)):
        raise ValueError('上传地址必须属于已核验的 302 文件主机')
    if parsed.hostname in {'file.302.ai', 'file.302ai.cn'}:
        return parsed._replace(netloc=FILE_ORIGIN).geturl()
    return audio_url


def submit_request(audio_url):
    return '/doubao/vc/submit?' + urlencode(PARAMETERS), {'url': public_audio_url(audio_url)}


def query_path(task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}', task_id):
        raise ValueError('任务 ID 无效')
    return '/doubao/vc/query?' + urlencode({'id': task_id, 'blocking': 0})


def _views(payload, inner_first=False):
    stack, count = [(payload, 0, False)], 0
    while stack:
        value, depth, expanded = stack.pop()
        count += 1
        if count > 100000 or depth > 16:
            raise ValueError('云端结果嵌套或数量超限')
        if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
            if len(value) > 16 * 1024 * 1024:
                raise ValueError('云端结果字符串超限')
            try:
                value = json.loads(value)
            except (ValueError, RecursionError):
                continue
        if isinstance(value, dict):
            if expanded:
                yield value
                continue
            if inner_first:
                stack.append((value, depth, True))
            else:
                yield value
            for key in reversed(ENVELOPES):
                if key in value:
                    stack.append((value[key], depth + 1, False))
        elif isinstance(value, list):
            if len(value) > 10000:
                raise ValueError('云端列表数量超限')
            stack.extend((item, depth + 1, False) for item in reversed(value))


def first_field(payload, *fields):
    for view in _views(payload, inner_first=True):
        for field in fields:
            value = view.get(field)
            if value is not None and value != '':
                return value
    return None


def provider_code(payload):
    for view in _views(payload, inner_first=True):
        for field in ('code', 'status_code', 'err_code', 'errCode'):
            value = view.get(field)
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                continue
            value = str(value).strip().casefold()
            if value:
                return value
    return None


def provider_message(payload):
    value = first_field(payload, 'message', 'error_message', 'fail_reason', 'message_cn', 'error')
    return value[:300] if isinstance(value, str) else ''


def utterances(payload):
    for view in _views(payload):
        rows = view.get('utterances')
        if isinstance(rows, list) and rows:
            if len(rows) > 10000 or any(not isinstance(row, dict) for row in rows):
                raise ValueError('字幕分句结构异常')
            return rows
    return []


def task_state(payload):
    code = provider_code(payload)
    status = str(first_field(payload, 'status', 'state') or '').strip().casefold()
    if code == '-10006':
        return 'quota_exhausted'
    if status in FAILED or code == '401':
        return 'failed'
    if status in PROCESSING or code in {'2000', '2001'}:
        return 'processing'
    if code in {'1002', '1003', '1004', '1005'}:
        return 'unknown'
    completed = {'complete', 'success', 'done', 'completed', 'succeeded'}
    if utterances(payload) and (code in SUCCESS or status in completed or (code is None and not status)):
        return 'success'
    if code in SUCCESS or status in completed:
        return 'empty'
    if code is None and not status:
        marker = first_field(payload, 'id', 'task_id', 'taskId', 'created_at', 'createdAt',
                             'updated_at', 'updatedAt', 'queue_position', 'queuePosition')
        if not isinstance(marker, bool) and isinstance(marker, (str, int, float)) and str(marker).strip():
            return 'processing'
        progress = first_field(payload, 'progress', 'percent', 'percentage')
        if not isinstance(progress, bool) and isinstance(progress, (int, float)) and math.isfinite(progress):
            return 'processing'
    return 'unknown'


def submitted_id(payload):
    if provider_code(payload) not in {'0', '1000', '200', 'ok', 'success'}:
        raise ValueError('提交未确认成功；保留回执，不重复提交')
    value = first_field(payload, 'id', 'task_id', 'taskId')
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('提交响应缺少有效任务 ID；保留回执，不重复提交')
    value = str(value).strip()
    query_path(value)
    return value


def _ms(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError('云端时间码格式无效')
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError('云端时间码必须有限且非负')
    return number


def parse_timeline(payload, version_id, duration_ms, trim_head_ms=0):
    """Preserve provider token intervals; both protocols use milliseconds."""
    if not isinstance(version_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', version_id):
        raise ValueError('必须绑定已核验的实际音频 SHA256')
    duration_ms, offset = _ms(duration_ms), _ms(trim_head_ms)
    rows = utterances(payload)
    if task_state(payload) != 'success' or not rows:
        raise ValueError('尚未取得成功且包含字幕的结果')
    segments, all_words = [], True
    for row in rows:
        text = row.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError('云端句文本无效')
        segment = {'text': text, 'startMs': _ms(row.get('start_time')) + offset,
                   'endMs': _ms(row.get('end_time')) + offset}
        source_words, words = row.get('words'), []
        if isinstance(source_words, list) and source_words:
            if len(source_words) > 500:
                raise ValueError('云端词数量超限')
            for word in source_words:
                if not isinstance(word, dict) or not isinstance(word.get('text'), str) or not word['text']:
                    raise ValueError('云端词结构异常')
                words.append({'text': word['text'],
                              'startMs': _ms(word.get('start_time')) + offset,
                              'endMs': _ms(word.get('end_time')) + offset})
            # Validate intervals even when the provider word/line texts differ.
            check_segment = dict(segment, text=''.join(w['text'] for w in words), words=words)
            validate_timeline({'schemaVersion': 1, 'versionId': version_id,
                               'durationMs': duration_ms, 'quality': 'word',
                               'segments': [check_segment]}, version_id)
            if check_segment['text'] != text:
                all_words = False
            else:
                segment['words'] = words
        else:
            all_words = False
        segments.append(segment)
    if not all_words:
        for segment in segments:
            segment.pop('words', None)
    timeline = {'schemaVersion': 1, 'versionId': version_id, 'durationMs': duration_ms,
                'quality': 'word' if all_words else 'sentence', 'segments': segments,
                'provider': PROVIDER, 'timingProvenance': 'provider_token' if all_words else 'provider_sentence',
                'acousticAccuracyVerified': False, 'trimHeadMs': offset}
    return validate_timeline(timeline, version_id)
