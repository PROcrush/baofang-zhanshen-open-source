"""Durable, at-most-once 302 singing jobs. Importing this module performs no I/O.

Official contracts: https://doc.302.ai/232294379e0 (file upload) and
https://doc.302.ai/ru/242284145e0 (singing submission). The cost is a local
estimate, not a provider-enforced spending limit. Tests inject a transport.
"""
import argparse
import decimal
import fcntl
import hashlib
import http.client
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
import wave
from pathlib import Path
from urllib.parse import urlsplit

from worker import doubao

UPLOAD_LIMIT_BYTES = 50_000_000  # Conservative interpretation of documented 50 MB.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
JOB_KIND = 'baofang-recognition'
PROOF_FIELDS = ('size', 'mtimeNs', 'inode', 'ctimeNs', 'device')
PLAN_FIELDS = ('schemaVersion', 'provider', 'versionId', 'sourcePath', 'uploadSource',
               'durationMs', 'size', 'sha256', 'host', 'estimatedCostPTC', 'maxCostPTC',
               'budgetAllowed', 'identityProof', 'uploadAllowed', 'uploadLimitBytes',
               'estimateOnly', 'costEstimateBasis', 'trimHeadMs', 'planId', 'intentId',
               'sessionRemainingPTC', 'route', 'routeLabel', 'title', 'service',
               'durationMethod', 'durationBasis')


def _host(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ValueError('服务路线必须属于已核验的 302 API 主机')
    parsed = urlsplit(value if '://' in value else 'https://' + value)
    if (parsed.scheme != 'https' or parsed.hostname not in doubao.HOSTS
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ValueError('服务路线必须属于已核验的 302 API 主机')
    return 'https://' + parsed.hostname


def _number(value, label, *, positive=False):
    if isinstance(value, bool):
        raise ValueError(label + '必须是有限的非负数值')
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise ValueError(label + '必须是有限的非负数值')
    return value


def _proof(path, info):
    return {'path': str(path), 'size': info.st_size, 'mtimeNs': info.st_mtime_ns,
            'inode': info.st_ino, 'ctimeNs': info.st_ctime_ns, 'device': info.st_dev}


def _same_proof(left, right):
    return left.get('path') == right.get('path') and all(left.get(k) == right.get(k) for k in PROOF_FIELDS)


def _source(path):
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError('歌曲缺少本机音频路径')
    source = Path(path).expanduser().resolve(strict=True)
    if source.suffix.lower() not in {'.wav', '.mp3', '.flac', '.m4a', '.aiff', '.aif', '.ogg'}:
        raise ValueError('不支持该音频文件类型')
    if not stat.S_ISREG(source.stat().st_mode):
        raise ValueError('音频源必须是本机普通文件')
    return source


def _ffprobe():
    supplied = os.environ.get('BAOFANG_FFPROBE', '')
    candidates = [supplied, shutil.which('ffprobe'), '/opt/homebrew/bin/ffprobe', '/usr/local/bin/ffprobe']
    return next((str(Path(p).expanduser()) for p in candidates if p and os.path.isfile(p)
                 and os.access(p, os.X_OK)), None)


def _duration_info(source):
    executable = _ffprobe()
    if executable:
        try:
            result = subprocess.run([executable, '-v', 'error', '-select_streams', 'a',
                                     '-show_entries', 'format=duration:stream=codec_type,duration',
                                     '-of', 'json', str(source)], capture_output=True,
                                    timeout=15, check=False)
            if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
                raise ValueError('ffprobe 未能读取有效音频时长')
            data = json.loads(result.stdout)
            if not data.get('streams'):
                raise ValueError('文件未包含可识别的音频流')
            duration = data.get('format', {}).get('duration')
            if duration is None:
                duration = next((s.get('duration') for s in data['streams'] if s.get('duration')), None)
            return _number(duration, '音频时长', positive=True) * 1000, 'ffprobe', 'file_metadata'
        except subprocess.TimeoutExpired as exc:
            raise ValueError('音频时长核验超时') from exc
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise ValueError('ffprobe 音频时长输出无效') from exc
    if source.suffix.lower() == '.wav':
        try:
            with wave.open(str(source), 'rb') as audio:
                return (_number(audio.getnframes() / audio.getframerate() * 1000, '音频时长', positive=True),
                        'wave_frame_count', 'file_sample_count')
        except (wave.Error, EOFError, ZeroDivisionError) as exc:
            raise ValueError('WAV 音频时长核验失败') from exc
    try:
        from tinytag import TinyTag
        duration = TinyTag.get(source, tags=False, image=False).duration
        return _number(duration, '文件元数据音频时长', positive=True) * 1000, 'tinytag', 'file_metadata'
    except ImportError as exc:
        raise ValueError('本机缺少 ffprobe 与 TinyTag，尚不能核验此音频的文件时长') from exc
    except Exception as exc:
        raise ValueError('TinyTag 未能核验有限且有效的音频文件元数据时长') from exc


def _duration_ms(source):
    return _duration_info(source)[0]


def _estimate(duration_ms):
    minutes = decimal.Decimal(str(duration_ms)) / decimal.Decimal(60000)
    rounded = int(minutes.to_integral_value(rounding=decimal.ROUND_CEILING))
    return float(decimal.Decimal(rounded) * decimal.Decimal('0.01') + decimal.Decimal('0.001'))


def preflight(track, host, max_cost_ptc):
    """Hash and inspect the complete local audio, including when budget is zero."""
    track = {'path': track} if isinstance(track, (str, os.PathLike)) else track
    if not isinstance(track, dict):
        raise ValueError('歌曲信息必须是对象或本机路径')
    source, route = _source(track.get('path')), _host(host)
    budget = _number(max_cost_ptc, '单次费用上限')
    digest = hashlib.sha256()
    with source.open('rb') as handle:
        before = _proof(source, os.fstat(handle.fileno()))
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
        if not _same_proof(before, _proof(source, os.fstat(handle.fileno()))):
            raise ValueError('核验期间音频文件发生变化，请重新规划')
    duration, method, basis = _duration_info(source)
    if not _same_proof(before, _proof(source, source.stat())):
        raise ValueError('核验期间音频文件发生变化，请重新规划')
    sha = digest.hexdigest()
    version = 'sha256:' + sha
    if track.get('versionId') and track['versionId'] != version:
        raise ValueError('实际音频 SHA256 与选定曲目版本不一致，请重新核验')
    cost = _estimate(duration)
    return {'schemaVersion': 1, 'provider': doubao.PROVIDER, 'versionId': version,
            'sourcePath': str(source), 'uploadSource': str(source), 'durationMs': duration,
            'size': before['size'], 'sha256': sha, 'host': route, 'identityProof': before,
            'estimatedCostPTC': cost, 'maxCostPTC': budget, 'budgetAllowed': cost <= budget,
            'uploadAllowed': 0 < before['size'] <= UPLOAD_LIMIT_BYTES,
            'uploadLimitBytes': UPLOAD_LIMIT_BYTES, 'trimHeadMs': 0, 'estimateOnly': True,
            'durationMethod': method, 'durationBasis': basis,
            'costEstimateBasis': '完整音频；识别 ceil(分钟)×0.01 PTC + 单次上传 0.001 PTC；服务端未提供硬预算保证'}


def _atomic(path, payload):
    """Private atomic replace, with a durable file before publishing the name."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('任务文件不能是符号链接')
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read_json(path, limit=1024 * 1024):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError('任务文件必须是普通文件')
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError('任务文件超出大小界限')
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('任务文件必须是 JSON 对象')
    return value


def _private_payload(value, key):
    # A server may echo request metadata; never save the supplied credential.
    if isinstance(value, str):
        return value.replace(key, '[credential]') if key else value
    if isinstance(value, dict):
        return {_private_payload(str(k), key): _private_payload(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [_private_payload(item, key) for item in value]
    return value


class TransportError(RuntimeError):
    def __init__(self, message='服务请求未确认', *, payload=None, status=None):
        super().__init__(message)
        self.payload, self.status = payload, status


class HTTPTransport:
    """TLS only, no redirects/retries; bounded response, socket and upload time."""
    timeout = 30
    upload_deadline = 120

    def _connection(self, host):
        return http.client.HTTPSConnection(urlsplit(_host(host)).hostname, timeout=self.timeout)

    def _response(self, connection):
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise TransportError('服务响应超出大小界限', status=response.status)
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError):
            payload = {'unparsedResponse': body.decode('utf-8', errors='replace')}
            raise TransportError('服务响应不是有效 JSON', payload=payload, status=response.status)
        if not 200 <= response.status < 300:
            raise TransportError('服务 HTTP 请求未确认成功', payload=payload, status=response.status)
        return payload

    def upload(self, host, source_path, key):
        boundary = 'baofang-' + uuid.uuid4().hex
        source = Path(source_path)
        head = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; '
                'filename="audio' + source.suffix.lower() + '"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode('ascii')
        tail = ('\r\n--' + boundary + '--\r\n').encode('ascii')
        connection = self._connection(host)
        started = time.monotonic()
        try:
            with source.open('rb') as handle:
                size = os.fstat(handle.fileno()).st_size
                if not 0 < size <= UPLOAD_LIMIT_BYTES:
                    raise ValueError('音频超过上传界限')
                connection.putrequest('POST', '/302/upload-file')
                connection.putheader('Authorization', 'Bearer ' + key)
                connection.putheader('Content-Type', 'multipart/form-data; boundary=' + boundary)
                connection.putheader('Content-Length', str(len(head) + size + len(tail)))
                connection.endheaders()
                connection.send(head)
                remaining = size
                while remaining:
                    if time.monotonic() - started > self.upload_deadline:
                        raise TimeoutError('上传期限到达')
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError('上传期间文件长度变化')
                    connection.send(chunk)
                    remaining -= len(chunk)
                if handle.read(1):
                    raise ValueError('上传期间文件长度变化')
                connection.send(tail)
                return self._response(connection)
        finally:
            connection.close()

    def submit(self, host, path, body, key):
        return self._request(host, 'POST', path, key, body)

    def query(self, host, path, key):
        return self._request(host, 'GET', path, key)

    def _request(self, host, method, path, key, body=None):
        connection = self._connection(host)
        headers = {'Authorization': 'Bearer ' + key}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        try:
            connection.request(method, path, body=data, headers=headers)
            return self._response(connection)
        finally:
            connection.close()


def _plan_copy(plan):
    if not isinstance(plan, dict):
        raise ValueError('识别计划必须是对象')
    clean = {field: plan[field] for field in PLAN_FIELDS if field in plan}
    clean['host'] = _host(clean.get('host'))
    if clean.get('provider') != doubao.PROVIDER or clean.get('schemaVersion') != 1:
        raise ValueError('计划服务或版本不匹配')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', str(clean.get('versionId', ''))):
        raise ValueError('计划缺少已核验的音频 SHA256')
    if clean.get('sha256') != clean['versionId'][7:]:
        raise ValueError('计划的音频 SHA256 字段不一致')
    if clean.get('uploadSource') != clean.get('sourcePath') or clean.get('trimHeadMs') != 0:
        raise ValueError('此版本仅支持完整源音频，不支持未核验裁剪文件')
    proof = clean.get('identityProof')
    if not isinstance(proof, dict) or proof.get('path') != clean['sourcePath']:
        raise ValueError('计划缺少源文件身份凭据')
    clean['identityProof'] = {k: proof.get(k) for k in ('path',) + PROOF_FIELDS}
    for field in PROOF_FIELDS:
        if isinstance(proof.get(field), bool) or not isinstance(proof.get(field), int):
            raise ValueError('源文件身份凭据无效')
    duration = _number(clean.get('durationMs'), '音频时长', positive=True)
    if _number(clean.get('estimatedCostPTC'), '费用预估') != _estimate(duration):
        raise ValueError('计划费用与保守计费规则不一致')
    _number(clean.get('maxCostPTC'), '单次费用上限')
    if 'sessionRemainingPTC' in clean:
        _number(clean['sessionRemainingPTC'], '会话剩余额度')
    if clean.get('size') != proof['size']:
        raise ValueError('计划文件大小字段不一致')
    for field in ('planId', 'intentId'):
        if field in clean and not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}', str(clean[field])):
            raise ValueError('计划或提交意向 ID 无效')
    # Bound optional UI labels; they are never requests or credentials.
    for field in ('title', 'route', 'routeLabel', 'service', 'costEstimateBasis'):
        if field in clean:
            if not isinstance(clean[field], str) or len(clean[field]) > 1000:
                raise ValueError('计划标签无效')
    return clean


def _verify_source(plan):
    current = preflight({'path': plan['sourcePath'], 'versionId': plan['versionId']},
                        plan['host'], plan['maxCostPTC'])
    if (not _same_proof(plan['identityProof'], current['identityProof'])
            or abs(plan['durationMs'] - current['durationMs']) > 0.001
            or plan['size'] != current['size']):
        raise ValueError('源文件或时长已变化，请重新核验；原任务保留')


def _result(receipt, phase, detail):
    result = {'phase': phase, 'detail': detail, 'versionId': receipt.get('versionId', '')}
    for field in ('taskId', 'timelinePath'):
        if receipt.get(field):
            result[field] = receipt[field]
    return result


def run_job(plan, receipt_path, *, action, key, transport=None, notify=None, poll_limit=1):
    """Start once or query an already known ID. Network exceptions remain durable.

    Injectable transport methods: upload(host, source_path, key),
    submit(host, request_path, body, key), query(host, request_path, key).
    Each returns a decoded JSON payload, or raises TransportError/OSError.
    """
    empty = {'versionId': plan.get('versionId', '') if isinstance(plan, dict) else ''}
    receipt, lock_fd, writable_receipt = empty, None, False
    path = Path(receipt_path).expanduser().absolute()
    def emit(phase, detail):
        if notify:
            notify(_result(receipt, phase, detail))
    try:
        if action not in ('start', 'query'):
            raise ValueError('只支持新建或续查识别任务')
        if isinstance(poll_limit, bool) or not isinstance(poll_limit, int) or not 0 <= poll_limit <= 10:
            raise ValueError('单轮查询次数应为 0–10')
        if 'local-test' in path.parts:
            raise ValueError('仅能处理本项目新建任务，不能沿用历史测试回执')
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.parent.resolve() != path.parent:
            raise ValueError('任务路径不能经过符号链接')
        lock_fd = os.open(path.with_name(path.name + '.lock'), os.O_CREAT | os.O_RDWR
                          | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.fchmod(lock_fd, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _result(empty, 'failed', '该任务已有工作进程持有锁，未发送请求')
        requested = _plan_copy(plan)
        exists = path.exists()
        if exists:
            receipt = _read_json(path)
            if receipt.get('jobKind') != JOB_KIND or receipt.get('schemaVersion') != 1:
                raise ValueError('此回执不是本项目创建的识别任务，未发送请求')
            stored = _plan_copy(receipt.get('plan'))
            if any(requested.get(k) != stored.get(k) for k in ('versionId', 'host', 'sourcePath')):
                raise ValueError('续查路线或歌曲身份与原回执不一致，未发送请求')
            if any(receipt.get(k) != stored.get(k) for k in ('versionId', 'host', 'durationMs')):
                raise ValueError('任务回执绑定字段不一致，未发送请求')
            writable_receipt = True
            if action == 'start':
                return _result(receipt, 'unknown' if not receipt.get('taskId') else receipt.get('state', 'polling'),
                               '任务意向已存在；保留原回执，仅允许查询已知任务，不重复上传或提交')
            requested = stored
        elif action == 'query':
            raise ValueError('没有本项目已保存的任务回执，未发送请求')
        else:
            receipt = {'schemaVersion': 1, 'jobKind': JOB_KIND, 'provider': doubao.PROVIDER,
                       'plan': requested, 'versionId': requested['versionId'],
                       'host': requested['host'], 'durationMs': requested['durationMs'],
                       'estimatedCostPTC': requested['estimatedCostPTC'],
                       'reservedCostPTC': requested['estimatedCostPTC'],
                       'intentId': requested.get('intentId') or str(uuid.uuid4()),
                       'state': 'planned', 'stage': 'intent_saved', 'networkStarted': False,
                       'createdUnixNs': time.time_ns(), 'queryCount': 0}
            _atomic(path, receipt)
            writable_receipt = True
        if action == 'query' and not receipt.get('taskId'):
            return _result(receipt, 'unknown', '回执没有已确认任务 ID；不重复上传或提交，请核对原提交')
        if receipt.get('state') == 'ready':
            return _result(receipt, 'ready', '该任务已有完整歌词结果')
        if not isinstance(key, str) or not 1 <= len(key) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError('缺少有效的任务 Key；未发送请求')
        if action == 'start':
            cost = requested['estimatedCostPTC']
            if cost > requested['maxCostPTC'] or cost > requested.get('sessionRemainingPTC', math.inf):
                raise ValueError('识别预估费用超过可用上限；未发送请求')
            if not 0 < requested['size'] <= UPLOAD_LIMIT_BYTES:
                raise ValueError('完整音频超过 50 MB 保守上传界限；未发送请求')
        _verify_source(requested)
        transport = transport if transport is not None else HTTPTransport()
        def save():
            receipt['updatedUnixNs'] = time.time_ns()
            _atomic(path, receipt)
        def raw(stage, payload):
            raw_path = path.with_name('raw-' + stage + '-' + uuid.uuid4().hex + '.json')
            _atomic(raw_path, _private_payload(payload, key))
            receipt.setdefault('rawResponses', []).append(str(raw_path))
            save()
        if action == 'start':
            receipt.update(state='unknown', stage='upload_started', networkStarted=True)
            save()  # A crash from here is never permission to submit again.
            emit('uploading', '正在上传完整音频；任务意向已保存')
            upload = transport.upload(requested['host'], requested['uploadSource'], key)
            raw('upload', upload)
            if not isinstance(upload, dict) or doubao.provider_code(upload) != '200':
                raise ValueError('上传响应未确认成功，保留回执，不重复上传')
            audio_url = upload.get('data')
            if isinstance(audio_url, dict):
                audio_url = audio_url.get('url')
            request_path, body = doubao.submit_request(audio_url)
            _verify_source(requested)
            receipt.update(stage='submit_started', state='unknown')
            save()
            emit('submitting', '正在提交歌唱识别；同一任务只提交一次')
            submitted = transport.submit(requested['host'], request_path, body, key)
            raw('submit', submitted)
            # Preserve an independently valid ID even if the success code is unclear.
            candidate = doubao.first_field(submitted, 'id', 'task_id', 'taskId')
            if not isinstance(candidate, bool) and isinstance(candidate, (str, int)):
                candidate = str(candidate).strip()
                try:
                    doubao.query_path(candidate)
                except ValueError:
                    pass
                else:
                    receipt['taskId'] = candidate
                    save()
            task_id = doubao.submitted_id(submitted)
            receipt.update(taskId=task_id, state='polling', stage='task_created')
            save()
        else:
            doubao.query_path(receipt['taskId'])
        for _ in range(poll_limit):
            receipt.update(stage='query_started', state='polling')
            receipt['queryCount'] += 1
            save()
            emit('polling', '正在查询已保存任务；不新增上传或识别')
            queried = transport.query(requested['host'], doubao.query_path(receipt['taskId']), key)
            raw('query', queried)
            state = doubao.task_state(queried)
            if state == 'processing':
                receipt.update(state='polling', stage='query_pending')
                save()
                continue
            if state == 'success':
                timeline = doubao.parse_timeline(queried, requested['versionId'], requested['durationMs'], 0)
                _verify_source(requested)
                timeline_path = path.with_name('timeline-' + uuid.uuid4().hex + '.json')
                _atomic(timeline_path, timeline)
                receipt.update(state='ready', stage='timeline_saved', timelinePath=str(timeline_path))
                save()
                return _result(receipt, 'ready', '歌词结果已按实际音频 SHA256 保存；源毫秒时间码保持不变')
            if state in ('failed', 'quota_exhausted', 'empty'):
                receipt.update(state='failed', stage='query_' + state)
                save()
                return _result(receipt, 'failed', '云任务未返回可用歌词；原回执和已有歌词保留')
            receipt.update(state='unknown', stage='query_unknown')
            save()
            return _result(receipt, 'unknown', '云端查询状态未确认；已知任务 ID 可继续查询，不重复提交')
        receipt.update(state='polling', stage='query_pending')
        save()
        return _result(receipt, 'polling', '云端任务仍在处理中；已知任务 ID 可继续查询')
    except (Exception,) as exc:
        # Do not expose exception text: network errors may include signed URLs/key.
        changed = writable_receipt
        after_network = bool(changed and receipt.get('networkStarted'))
        phase = 'unknown' if after_network else 'failed'
        detail = '请求状态未确认；保留任务回执，不重复上传或提交' if after_network else '本机计划、文件、Key 或额度核验未通过；未发送请求'
        # ValueErrors we generated are safe, but avoid reflecting arbitrary transport errors.
        if isinstance(exc, ValueError) and not after_network:
            detail = str(exc)
        if changed:
            if isinstance(exc, TransportError) and exc.payload is not None:
                raw_path = path.with_name('raw-error-' + uuid.uuid4().hex + '.json')
                try:
                    _atomic(raw_path, _private_payload(exc.payload, key))
                    receipt.setdefault('rawResponses', []).append(str(raw_path))
                except (OSError, ValueError):
                    pass
            receipt.update(state=phase, lastErrorType=type(exc).__name__, lastDetail=detail,
                           updatedUnixNs=time.time_ns())
            try:
                _atomic(path, receipt)
            except (OSError, ValueError):
                detail += '；回执写入失败，请核对原文件'
        return _result(receipt, phase, detail)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description='本项目独立云识别工作进程；默认不执行网络请求')
    parser.add_argument('--action', choices=('preflight', 'start', 'query'), required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    def output(value):
        print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
    try:
        request = _read_json(args.plan)
        if args.action == 'preflight':
            output({'phase': 'preflight', 'detail': '正在核验完整源音频、SHA256、文件时长与费用预估'})
            planned = preflight(request.get('track'), request.get('host'), request.get('maxCostPTC', 0))
            for label in ('planId', 'route', 'routeLabel'):
                if label in request:
                    planned[label] = request[label]
            output({'phase': 'planned', 'plan': planned})
        else:
            # Read only the child-scoped credential, never a config/history receipt.
            output(run_job(request, args.receipt, action=args.action,
                           key=os.environ.get('BAOFANG_TASK_KEY', ''), notify=output))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else '本机预检输入或文件读取失败'
        output({'phase': 'failed', 'detail': detail})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
