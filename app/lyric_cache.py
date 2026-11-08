"""Portable, atomic lyric files. No audio, credentials, or cloud receipts."""
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

MAX_CACHE_BYTES = 4_000_000
VERSION_RE = re.compile(r'^sha256:([0-9a-f]{64})$')


def cache_filename(version_id):
    match = VERSION_RE.fullmatch(version_id) if isinstance(version_id, str) else None
    if not match:
        raise ValueError('本地歌词必须使用完整的 SHA256 音频版本')
    return match[1] + '.json'


def read_bytes(path, limit=MAX_CACHE_BYTES):
    """Read a bounded regular file, refusing links rather than following them."""
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as handle:
        details = os.fstat(handle.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > limit:
            raise ValueError('缓存文件不是普通文件或大小超限')
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError('缓存文件大小超限')
    return data


def read_json(path):
    return json.loads(read_bytes(path).decode('utf-8'))


def atomic_bytes(path, data):
    destination = Path(path)
    if destination.is_symlink():
        raise ValueError('缓存目标不能为符号链接')
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.lyric-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        # Persist the directory entry as well as the contents when supported.
        parent_fd = os.open(str(destination.parent), os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def encode_json(payload):
    data = (json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    if len(data) > MAX_CACHE_BYTES:
        raise ValueError('歌词缓存大小超限')
    return data


def _fields(value, keys):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if key in value and
            (value[key] is None or isinstance(value[key], (str, bool, int, float)))}


WORD_FIELDS = ('text', 'startMs', 'endMs')
CHANGE_FIELDS = ('segmentIndex', 'referenceLine', 'referenceSource', 'referenceLabel',
                 'originalText', 'correctedText', 'similarity', 'startMs', 'endMs', 'timingAction')
REVIEW_FIELDS = ('segmentIndex', 'reasonCode', 'originalText', 'bestSimilarity', 'referenceLine')


def source_timing_provenance(timeline):
    correction = timeline.get('correction')
    original = correction.get('originalTimingProvenance') if isinstance(correction, dict) else None
    return timeline.get('sourceTimingProvenance') or original or timeline.get('timingProvenance')


def lyric_source_label(timeline):
    if not isinstance(timeline, dict) or not timeline:
        return ''
    provider = str(timeline.get('provider') or '')
    timing = str(source_timing_provenance(timeline) or '')
    if provider.startswith('local-whisper') or timing.startswith('local_asr'):
        return '本地识别'
    if timing.startswith('imported_'):
        return '导入字幕'
    if timing.startswith('provider_') or provider in (
            '302-doubao-vc', '302-singing', 'bailian-asr', 'volc-asr', 'siliconflow-asr', 'compatible-asr'):
        return '云端识别'
    return '来源未标记'


def portable_timeline(payload):
    """Whitelist the lyric protocol and its correction evidence, never task data."""
    result = _fields(payload, ('schemaVersion', 'versionId', 'durationMs', 'quality',
                              'provider', 'timingProvenance', 'sourceTimingProvenance',
                              'acousticAccuracyVerified', 'trimHeadMs'))
    result['segments'] = []
    for source in payload['segments']:
        segment = _fields(source, ('text', 'startMs', 'endMs', 'originalText', 'timingProvenance'))
        if isinstance(source.get('words'), list):
            segment['words'] = [_fields(word, WORD_FIELDS) for word in source['words']]
        if isinstance(source.get('correctionProvenance'), dict):
            segment['correctionProvenance'] = _fields(source['correctionProvenance'], CHANGE_FIELDS)
        result['segments'].append(segment)
    correction = payload.get('correction')
    if isinstance(correction, dict):
        result['correction'] = _fields(correction, ('status', 'referenceSource', 'referenceLabel',
            'referenceSha256', 'referenceLineCount', 'automaticSearch', 'originalQuality',
            'originalTimingProvenance', 'timingRealigned'))
        for field, allowed in (('changes', CHANGE_FIELDS), ('review', REVIEW_FIELDS)):
            values = correction.get(field, [])
            if not isinstance(values, list) or len(values) > 10000:
                raise ValueError('纠错记录格式无效')
            result['correction'][field] = [_fields(value, allowed) for value in values]
            if field == 'changes':
                for original, clean in zip(values, result['correction'][field]):
                    if isinstance(original, dict) and isinstance(original.get('originalWords'), list):
                        clean['originalWords'] = [_fields(word, WORD_FIELDS) for word in original['originalWords']]
    return result


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def plain_directory(path, *, create=False):
    directory = Path(path).expanduser().absolute()
    if directory.is_symlink():
        raise ValueError('歌词库目录不能为符号链接')
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError('请选择歌词库文件夹')
    return directory
