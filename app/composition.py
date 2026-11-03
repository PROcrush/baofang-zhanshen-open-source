"""Validated millisecond editing model for our independent production engine.

All edits return new objects. This module neither reads files nor imports the
controller. A caller may inject identity_validator for current source checks.
"""

import copy
import hashlib
import json
import math
from pathlib import PurePath
import re
import uuid

EPSILON = 1e-6
_CLIP_KEYS = {'id', 'sourceId', 'kind', 'startMs', 'durationMs', 'sourceInMs', 'rate',
              'fit', 'locked', 'color', 'transition'}
_CANDIDATE_KEYS = {'purpose', 'reviewed', 'reason'}
_TEXT_KEYS = {'id', 'startMs', 'durationMs', 'text', 'x', 'y', 'fontSize', 'fontFamily',
              'fontBold', 'color', 'opacity', 'layoutId', 'animationId', 'locked'}
_AUDIO_KEYS = {'id', 'sourceId', 'startMs', 'durationMs', 'sourceInMs', 'rate', 'gain', 'locked'}
_SOURCE_KEYS = {'identity', 'kind', 'durationMs', 'width', 'height'}


def _number(value, label, low=None, high=None):
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(label + ' 必须是有限数值')
    if low is not None and value < low or high is not None and value > high:
        raise ValueError(label + ' 超出范围')
    return value


def _object(value, required, optional=frozenset(), label='对象'):
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ValueError(label + ' 字段无效')


def _identifier(value, label='ID', allow_empty=False):
    if value == '' and allow_empty:
        return value
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError(label + ' 无效')
    return value


def _string(value, label, limit=500, empty=False):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value.strip())
            or any(ord(char) < 32 and char not in '\n\t' for char in value) or '\x7f' in value):
        raise ValueError(label + ' 内容无效')
    return value


def _bool(value, label):
    if type(value) is not bool:
        raise ValueError(label + ' 必须是布尔值')


def _color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('颜色必须是 #RRGGBB')


def _finite_json(value, depth=0):
    if depth > 32:
        raise ValueError('工程数据嵌套过深')
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _finite_json(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _finite_json(item, depth + 1)
        return
    raise ValueError('工程数据必须是有限 JSON 值')


def _identity(value, validator):
    _object(value, {'path', 'size', 'mtimeNs', 'device', 'inode', 'sha256'}, label='素材身份')
    _string(value['path'], '素材路径', 8192)
    if not PurePath(value['path']).is_absolute():
        raise ValueError('素材路径必须是绝对路径')
    for key in ('size', 'mtimeNs', 'device', 'inode'):
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError('素材身份数值无效')
    if not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256']):
        raise ValueError('素材 SHA256 无效')
    if validator is not None:
        current = validator(copy.deepcopy(value))
        if current is False or isinstance(current, dict) and current != value:
            raise ValueError('素材身份已经变化')


def frame_snap_ms(value, fps=24, mode='nearest'):
    """Return an exact frame-grid millisecond value (floor for safe transitions)."""
    _number(value, '帧时间', 0)
    _number(fps, '帧率', 1, 60)
    frames = value * fps / 1000
    if mode == 'floor':
        frames = math.floor(frames + 1e-9)
    elif mode == 'ceil':
        frames = math.ceil(frames - 1e-9)
    elif mode == 'nearest':
        frames = math.floor(frames + .5)
    else:
        raise ValueError('帧对齐模式无效')
    return frames * 1000 / fps


def _frame_aligned(value, fps):
    return abs(value * fps / 1000 - round(value * fps / 1000)) < 1e-7


def empty_composition():
    return {'version': 1, 'sources': {}, 'clips': [], 'texts': [], 'audioClips': [],
            'markers': [], 'concert': {}, 'fps': 24}


def _time(row, duration_ms):
    _number(row['startMs'], '开始时间', 0)
    _number(row['durationMs'], '片段时长', 0)
    if row['durationMs'] <= 0 or row['startMs'] + row['durationMs'] > duration_ms + EPSILON:
        raise ValueError('片段必须位于主歌曲的有效时间内')


def _source_clock(row, source, kind):
    _number(row['sourceInMs'], '素材入点', 0)
    _number(row['rate'], '播放速度', .25, 4)
    if kind in ('image', 'color'):
        if row['sourceInMs'] != 0 or row['rate'] != 1:
            raise ValueError('图片与颜色背景没有素材入点或变速')
    elif row['sourceInMs'] + row['durationMs'] * row['rate'] > source['durationMs'] + EPSILON:
        raise ValueError('裁剪或变速超出素材时长')


def validate_composition(value, duration_ms, identity_validator=None):
    """Strictly validate and copy; callback receives each identity, not a path."""
    _number(duration_ms, '主歌曲时长', 0)
    _object(value, {'version', 'sources', 'clips', 'texts', 'audioClips', 'markers', 'concert'},
            {'fps'}, '制作工程')
    _finite_json(value)
    if duration_ms == 0 and any(value[key] for key in ('clips', 'texts', 'audioClips', 'markers')):
        raise ValueError('尚未建立主歌曲时钟，时间线必须为空')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('制作工程版本无效')
    fps = _number(value.get('fps', 24), '帧率', 1, 60)
    if not isinstance(value['sources'], dict) or len(value['sources']) > 1000:
        raise ValueError('素材目录无效')
    for key, source in value['sources'].items():
        _identifier(key, '素材 ID')
        _object(source, _SOURCE_KEYS, {'hasAudio'}, '素材')
        _identity(source['identity'], identity_validator)
        if source['kind'] not in ('image', 'video', 'audio'):
            raise ValueError('素材类型无效')
        if 'hasAudio' in source:
            _bool(source['hasAudio'], '素材音频状态')
            if source['kind'] == 'image' and source['hasAudio']:
                raise ValueError('图片不能带音频')
        _number(source['durationMs'], '素材时长', 0)
        if source['kind'] == 'image' and source['durationMs'] != 0:
            raise ValueError('图片时长必须是零')
        if source['kind'] != 'image' and source['durationMs'] <= 0:
            raise ValueError('音视频时长必须大于零')
        for dimension in ('width', 'height'):
            if type(source[dimension]) is not int or not 0 <= source[dimension] <= 16384:
                raise ValueError('素材尺寸无效')
            if source['kind'] != 'audio' and source[dimension] == 0:
                raise ValueError('画面素材尺寸必须大于零')
    all_ids = set()
    for key, maximum in [('clips', 10000), ('texts', 200), ('audioClips', 200), ('markers', 10000)]:
        if not isinstance(value[key], list) or len(value[key]) > maximum:
            raise ValueError(key + ' 列表无效')
        for row in value[key]:
            if not isinstance(row, dict):
                raise ValueError('时间线行必须是对象')
            identifier = _identifier(row.get('id'))
            if identifier in all_ids:
                raise ValueError('时间线 ID 重复')
            all_ids.add(identifier)
    previous = None
    for row in value['clips']:
        _object(row, _CLIP_KEYS, _CANDIDATE_KEYS, '画面片段')
        _time(row, duration_ms)
        _identifier(row['sourceId'], '素材 ID', allow_empty=True)
        if row['kind'] not in ('video', 'image', 'color') or row['fit'] not in ('cover', 'contain'):
            raise ValueError('片段类型或缩放方式无效')
        _bool(row['locked'], '片段锁定')
        _color(row['color'])
        source = value['sources'].get(row['sourceId'])
        if row['kind'] == 'color':
            if row['sourceId'] != '':
                raise ValueError('颜色背景不能指定素材')
        elif source is None or source['kind'] != row['kind']:
            raise ValueError('片段与素材类型不一致')
        _source_clock(row, source, row['kind'])
        if 'purpose' in row:
            if row['purpose'] not in ('paired', 'cutaway', 'gap'):
                raise ValueError('候选用途无效')
            if 'reviewed' not in row or 'reason' not in row:
                raise ValueError('候选片段必须带复核状态与说明')
        elif 'reviewed' in row or 'reason' in row:
            raise ValueError('复核字段必须与候选用途一起保存')
        if 'reviewed' in row:
            _bool(row['reviewed'], '候选复核')
            _string(row['reason'], '候选说明', 1000, empty=True)
        transition = row['transition']
        _object(transition, {'id', 'durationMs'}, label='转场')
        _identifier(transition['id'], '转场 ID')
        _number(transition['durationMs'], '转场时长', 0)
        if previous and row['startMs'] < previous['startMs'] + previous['durationMs'] - EPSILON:
            raise ValueError('画面片段必须按时间排序且不能重叠')
        if transition['id'] == 'cut':
            if transition['durationMs'] != 0:
                raise ValueError('直接切换的转场时长必须为零')
        else:
            if (previous is None or abs(row['startMs'] - previous['startMs'] - previous['durationMs']) > EPSILON
                    or transition['durationMs'] <= 0
                    or transition['durationMs'] > min(previous['durationMs'], row['durationMs']) / 2 + EPSILON):
                raise ValueError('转场需要邻接片段且不得超过两侧时长的一半')
            if not _frame_aligned(row['startMs'], fps) or not _frame_aligned(transition['durationMs'], fps):
                raise ValueError('转场边界与时长必须对齐输出帧率')
        previous = row
    events = []
    for row in value['texts']:
        _object(row, _TEXT_KEYS, label='独立文字')
        _time(row, duration_ms)
        _string(row['text'], '独立文字', 120)
        if len(row['text'].splitlines()) > 8:
            raise ValueError('独立文字最多八行')
        for coordinate in ('x', 'y'):
            _number(row[coordinate], coordinate, 0, 1)
        _number(row['fontSize'], '字体大小', 8, 200)
        _number(row['opacity'], '不透明度', 0, 1)
        _string(row['fontFamily'], '字体', 200)
        _bool(row['fontBold'], '字重')
        _bool(row['locked'], '文字锁定')
        _color(row['color'])
        for key in ('layoutId', 'animationId'):
            _identifier(row[key], key)
        events.extend([(row['startMs'], 1), (row['startMs'] + row['durationMs'], -1)])
    concurrent = 0
    for _, change in sorted(events):
        concurrent += change
        if concurrent > 12:
            raise ValueError('同一时刻最多十二个独立文字')
    for row in value['audioClips']:
        _object(row, _AUDIO_KEYS, label='附加音轨')
        _time(row, duration_ms)
        _identifier(row['sourceId'], '音轨素材 ID')
        source = value['sources'].get(row['sourceId'])
        if (not source or source['kind'] not in ('audio', 'video')
                or source.get('hasAudio') is False):
            raise ValueError('附加音轨必须使用有声音的音视频素材')
        _source_clock(row, source, 'audio')
        _number(row['gain'], '音轨增益', 0, 2)
        _bool(row['locked'], '音轨锁定')
    for row in value['markers']:
        _object(row, {'id', 'positionMs', 'label'}, label='标记')
        _number(row['positionMs'], '标记时间', 0, duration_ms)
        _string(row['label'], '标记文字', 200)
    concert = value['concert']
    if not isinstance(concert, dict):
        raise ValueError('演唱会计划无效')
    if concert:
        _object(concert, {'sourceId', 'sourceSegments', 'reviewed'},
                {'repeatedCandidates', 'candidateOnly', 'acousticTimingVerified', 'visualSafetyVerified',
                 'lyricsPath', 'lyricsIdentity', 'gapSourceId', 'targetDigest', 'pairing', 'coverage'}, '演唱会计划')
        source = value['sources'].get(concert['sourceId'])
        if not source or source['kind'] != 'video':
            raise ValueError('演唱会计划的源视频无效')
        from app.studio_tools import validate_segments
        source_lyrics = validate_segments(concert['sourceSegments'], duration=source['durationMs'] / 1000)
        _bool(concert['reviewed'], '演唱会复核')
        if 'lyricsPath' in concert:
            _string(concert['lyricsPath'], '演唱会歌词路径', 8192, empty=True)
            if concert['lyricsPath'] and not PurePath(concert['lyricsPath']).is_absolute():
                raise ValueError('演唱会歌词路径必须是绝对路径')
        if 'lyricsIdentity' in concert:
            _identity(concert['lyricsIdentity'], identity_validator)
            if 'lyricsPath' in concert and concert['lyricsPath'] != concert['lyricsIdentity']['path']:
                raise ValueError('演唱会歌词来源身份不一致')
        if 'gapSourceId' in concert:
            _identifier(concert['gapSourceId'], '补镜素材 ID', allow_empty=True)
            filler = value['sources'].get(concert['gapSourceId'])
            if concert['gapSourceId'] and (not filler or filler['kind'] not in ('image', 'video')):
                raise ValueError('演唱会补镜素材无效')
        for flag in ('candidateOnly', 'acousticTimingVerified', 'visualSafetyVerified'):
            if flag in concert:
                _bool(concert[flag], flag)
                if concert[flag] != (flag == 'candidateOnly'):
                    raise ValueError('歌词候选不能声明声学同步或画面已经核验')
        plan_keys = {'targetDigest', 'pairing', 'coverage'}
        if plan_keys & set(concert):
            if not plan_keys <= set(concert):
                raise ValueError('演唱会计划字段必须一起保存')
            if not isinstance(concert['targetDigest'], str) or not re.fullmatch('[0-9a-f]{64}', concert['targetDigest']):
                raise ValueError('目标歌词摘要无效')
            if not isinstance(concert['pairing'], dict):
                raise ValueError('演唱会候选数据无效')
            from app.studio_tools import map_concert_segments
            map_concert_segments(concert['pairing'])
            pairing = concert['pairing']
            if pairing['sourceSegments'] != source_lyrics:
                raise ValueError('演唱会候选源歌词与导入稿不一致')
            target_lyrics = validate_segments(pairing['targetSegments'], duration=duration_ms / 1000)
            target_digest = hashlib.sha256(json.dumps(target_lyrics, ensure_ascii=False, sort_keys=True,
                                                      separators=(',', ':')).encode('utf-8')).hexdigest()
            if concert['targetDigest'] != target_digest:
                raise ValueError('演唱会目标歌词与摘要不一致')
            from worker.concert import concert_coverage
            expected = concert_coverage(value['clips'], duration_ms)
            if concert['coverage'] != expected or concert['reviewed'] != expected['reviewed']:
                raise ValueError('演唱会覆盖或复核统计与片段不一致')
        elif concert['reviewed']:
            raise ValueError('尚未生成演唱会计划，不能标记复核完成')
    return copy.deepcopy(value)


def make_clip(source_id, kind, start_ms, duration_ms, **options):
    unknown = set(options) - ((_CLIP_KEYS | _CANDIDATE_KEYS) - {'sourceId', 'kind', 'startMs', 'durationMs'})
    if unknown:
        raise ValueError('未知片段参数：' + ','.join(sorted(unknown)))
    result = {'id': 'clip-' + uuid.uuid4().hex, 'sourceId': source_id, 'kind': kind,
              'startMs': start_ms, 'durationMs': duration_ms, 'sourceInMs': 0, 'rate': 1,
              'fit': 'cover', 'locked': False, 'color': '#141c36',
              'transition': {'id': 'cut', 'durationMs': 0}}
    result.update(copy.deepcopy(options))
    _number(start_ms, '片段开始时间', 0)
    _number(duration_ms, '片段时长', 0)
    if duration_ms <= 0:
        raise ValueError('片段时长必须大于零')
    _finite_json(result)
    _identifier(result['id'])
    _identifier(source_id, '素材 ID', allow_empty=kind == 'color')
    if kind not in ('video', 'image', 'color') or result['fit'] not in ('cover', 'contain'):
        raise ValueError('片段类型或缩放方式无效')
    if kind == 'color' and source_id:
        raise ValueError('颜色背景不能指定素材')
    _number(result['sourceInMs'], '素材入点', 0)
    _number(result['rate'], '播放速度', .25, 4)
    if kind in ('image', 'color') and (result['rate'] != 1 or result['sourceInMs'] != 0):
        raise ValueError('图片与颜色背景没有素材入点或变速')
    _bool(result['locked'], '片段锁定')
    _color(result['color'])
    _check_transition(result['transition'])
    if _CANDIDATE_KEYS & set(result):
        if not _CANDIDATE_KEYS <= set(result) or result['purpose'] not in ('paired', 'cutaway', 'gap'):
            raise ValueError('候选片段字段无效')
        _bool(result['reviewed'], '候选复核')
        _string(result['reason'], '候选说明', 1000, empty=True)
    return result


def make_text(text, start_ms, duration_ms, **options):
    if set(options) - (_TEXT_KEYS - {'text', 'startMs', 'durationMs'}):
        raise ValueError('未知独立文字参数')
    result = {'id': 'text-' + uuid.uuid4().hex, 'startMs': start_ms, 'durationMs': duration_ms,
              'text': text, 'x': .5, 'y': .5, 'fontSize': 42, 'fontFamily': 'PingFang SC',
              'fontBold': True, 'color': '#ffffff', 'opacity': 1, 'layoutId': 'standard',
              'animationId': 'none', 'locked': False}
    result.update(copy.deepcopy(options))
    probe = empty_composition()
    probe['texts'] = [result]
    validate_composition(probe, _number(start_ms, '文字开始时间', 0) + _number(duration_ms, '文字时长', 0))
    return result


def make_audio_clip(source_id, start_ms, duration_ms, **options):
    if set(options) - (_AUDIO_KEYS - {'sourceId', 'startMs', 'durationMs'}):
        raise ValueError('未知音轨参数')
    result = {'id': 'audio-' + uuid.uuid4().hex, 'sourceId': source_id, 'startMs': start_ms,
              'durationMs': duration_ms, 'sourceInMs': 0, 'rate': 1, 'gain': 1, 'locked': False}
    result.update(copy.deepcopy(options))
    _finite_json(result)
    _identifier(result['id'])
    _identifier(source_id, '音轨素材 ID')
    _number(start_ms, '音轨开始时间', 0)
    _number(duration_ms, '音轨时长', 0)
    if duration_ms <= 0:
        raise ValueError('音轨时长必须大于零')
    _number(result['sourceInMs'], '素材入点', 0)
    _number(result['rate'], '播放速度', .25, 4)
    _number(result['gain'], '音轨增益', 0, 2)
    _bool(result['locked'], '音轨锁定')
    return result


def _refresh_concert(value, duration_ms):
    if value['concert'] and 'coverage' in value['concert']:
        from worker.concert import concert_coverage
        value['concert']['coverage'] = concert_coverage(value['clips'], duration_ms)
        value['concert']['reviewed'] = value['concert']['coverage']['reviewed']


def refresh_concert_coverage(composition, duration_ms):
    """Recompute candidate confirmation counters before strict validation."""
    value = copy.deepcopy(composition)
    _finite_json(value)
    _refresh_concert(value, duration_ms)
    return validate_composition(value, duration_ms)


def _repair_transitions(clips, fps):
    previous = None
    for row in clips:
        transition = row['transition']
        original = copy.deepcopy(transition)
        if transition['id'] != 'cut':
            adjacent = previous and abs(row['startMs'] - previous['startMs'] - previous['durationMs']) < EPSILON
            if not adjacent or not _frame_aligned(row['startMs'], fps):
                row['transition'] = {'id': 'cut', 'durationMs': 0}
            else:
                length = frame_snap_ms(min(transition['durationMs'], previous['durationMs'] / 2,
                                           row['durationMs'] / 2), fps, 'floor')
                row['transition'] = {'id': transition['id'], 'durationMs': length} if length else {'id': 'cut', 'durationMs': 0}
        if row['locked'] and row['transition'] != original:
            raise ValueError('操作会改变已锁定片段的转场，请先解锁')
        previous = row


def _check_transition(transition):
    _object(transition, {'id', 'durationMs'}, label='转场')
    _identifier(transition['id'], '转场 ID')
    _number(transition['durationMs'], '转场时长', 0)
    if (transition['id'] == 'cut' and transition['durationMs'] != 0
            or transition['id'] != 'cut' and transition['durationMs'] <= 0):
        raise ValueError('转场 ID 与时长不一致')


def _locate(value, collection, identifier):
    for index, row in enumerate(value[collection]):
        if row['id'] == identifier:
            return index, row
    raise ValueError('找不到指定时间线片段')


def _edit(composition, collection, identifier, changes, duration_ms, allowed):
    value = validate_composition(composition, duration_ms)
    if not isinstance(changes, dict) or not changes or set(changes) - allowed:
        raise ValueError('编辑字段无效')
    _finite_json(changes)
    _, row = _locate(value, collection, identifier)
    if row.get('locked') and set(changes) not in ({'locked'}, {'reviewed'}):
        raise ValueError('片段已锁定，请先解锁')
    if 'reviewed' in changes and set(changes) != {'reviewed'}:
        raise ValueError('人工复核需要单独确认当前片段')
    if 'reviewed' in changes and 'purpose' not in row:
        raise ValueError('仅演唱会候选片段可以人工复核')
    if 'transition' in changes:
        _check_transition(changes['transition'])
    row.update(copy.deepcopy(changes))
    if 'purpose' in row and set(changes) - {'locked', 'reviewed'}:
        row['reviewed'] = False
    value[collection].sort(key=lambda item: item.get('startMs', 0))
    if collection == 'clips':
        if 'transition' in changes and row['transition']['id'] != 'cut':
            index = value['clips'].index(row)
            previous = value['clips'][index - 1] if index else None
            if (not previous or abs(row['startMs'] - previous['startMs'] - previous['durationMs']) > EPSILON
                    or not _frame_aligned(row['startMs'], value.get('fps', 24))):
                raise ValueError('转场只允许在帧对齐的邻接边界使用')
        _repair_transitions(value['clips'], value.get('fps', 24))
    _refresh_concert(value, duration_ms)
    return validate_composition(value, duration_ms)


def edit_clip(composition, clip_id, changes, duration_ms):
    return _edit(composition, 'clips', clip_id, changes, duration_ms, (_CLIP_KEYS | _CANDIDATE_KEYS) - {'id'})


def set_clip_locked(composition, clip_id, locked, duration_ms):
    return edit_clip(composition, clip_id, {'locked': locked}, duration_ms)


def edit_text(composition, text_id, changes, duration_ms):
    return _edit(composition, 'texts', text_id, changes, duration_ms, _TEXT_KEYS - {'id'})


def edit_audio_clip(composition, clip_id, changes, duration_ms):
    return _edit(composition, 'audioClips', clip_id, changes, duration_ms, _AUDIO_KEYS - {'id'})


def split_clip(composition, clip_id, position_ms, duration_ms):
    value = validate_composition(composition, duration_ms)
    index, row = _locate(value, 'clips', clip_id)
    _number(position_ms, '拆分时间', 0)
    if row['locked']:
        raise ValueError('片段已锁定，请先解锁')
    elapsed = position_ms - row['startMs']
    if not 0 < elapsed < row['durationMs']:
        raise ValueError('拆分点必须在片段内部')
    right = copy.deepcopy(row)
    right.update(id='clip-' + uuid.uuid4().hex, startMs=position_ms, durationMs=row['durationMs'] - elapsed,
                 sourceInMs=row['sourceInMs'] + elapsed * row['rate'] if row['kind'] == 'video' else 0,
                 transition={'id': 'cut', 'durationMs': 0})
    row['durationMs'] = elapsed
    if 'purpose' in row:
        row['reviewed'] = right['reviewed'] = False
    value['clips'].insert(index + 1, right)
    _repair_transitions(value['clips'], value.get('fps', 24))
    _refresh_concert(value, duration_ms)
    return validate_composition(value, duration_ms)


def _remove(composition, collection, identifier, duration_ms):
    value = validate_composition(composition, duration_ms)
    index, row = _locate(value, collection, identifier)
    if row.get('locked'):
        raise ValueError('片段已锁定，请先解锁')
    del value[collection][index]
    if collection == 'clips':
        _repair_transitions(value['clips'], value.get('fps', 24))
    _refresh_concert(value, duration_ms)
    return validate_composition(value, duration_ms)


def remove_clip(composition, clip_id, duration_ms):
    return _remove(composition, 'clips', clip_id, duration_ms)


def remove_text(composition, text_id, duration_ms):
    return _remove(composition, 'texts', text_id, duration_ms)


def remove_audio_clip(composition, clip_id, duration_ms):
    return _remove(composition, 'audioClips', clip_id, duration_ms)


def add_marker(composition, position_ms, label, duration_ms):
    value = validate_composition(composition, duration_ms)
    value['markers'].append({'id': 'marker-' + uuid.uuid4().hex, 'positionMs': position_ms, 'label': label})
    value['markers'].sort(key=lambda row: row['positionMs'])
    return validate_composition(value, duration_ms)


def remove_marker(composition, marker_id, duration_ms):
    return _remove(composition, 'markers', marker_id, duration_ms)


def _fill_clips(value, source_id, start, duration, **options):
    """Tile a finite video rather than inventing source frames beyond its end."""
    source = value['sources'].get(source_id) if source_id else None
    if source_id and (not source or source['kind'] not in ('image', 'video')):
        raise ValueError('补镜只能使用图片或视频')
    kind = source['kind'] if source else 'color'
    result, remaining = [], duration
    while remaining > EPSILON:
        length = min(remaining, source['durationMs']) if kind == 'video' else remaining
        result.append(make_clip(source_id, kind, start, length, **options))
        if len(result) > 10000:
            raise ValueError('素材太短，补镜片段数量超出上限')
        start += length
        remaining -= length
    return result


def _seconds_segments(segments, duration_ms):
    from app.studio_tools import validate_segments
    if not isinstance(segments, list):
        raise ValueError('歌词列表无效')
    seconds = []
    for row in segments:
        if not isinstance(row, dict):
            raise ValueError('歌词行无效')
        if 'startMs' in row or 'endMs' in row:
            start = _number(row.get('startMs'), '歌词开始时间', 0) / 1000
            end = _number(row.get('endMs'), '歌词结束时间', 0) / 1000
            seconds.append({'start': start, 'end': end, 'text': row.get('text')})
        else:
            seconds.append(copy.deepcopy(row))
    return validate_segments(seconds, duration=duration_ms / 1000)


def generate_storyboard(composition, segments, duration_ms, source_ids=None, default_transition='cut'):
    """Cover the song with lyric-based boundaries and independently selected media."""
    value = validate_composition(composition, duration_ms)
    if any(row['locked'] for row in value['clips']):
        raise ValueError('自动分镜前请解锁已有画面片段')
    _identifier(default_transition, '转场 ID')
    rows = _seconds_segments(segments, duration_ms)
    identifiers = list(value['sources']) if source_ids is None else source_ids
    if not isinstance(identifiers, list):
        raise ValueError('分镜素材 ID 必须是列表')
    if source_ids is None:
        identifiers = [key for key in identifiers if value['sources'][key]['kind'] in ('video', 'image')]
    for key in identifiers:
        if key not in value['sources'] or value['sources'][key]['kind'] not in ('video', 'image'):
            raise ValueError('分镜素材必须是已导入的图片或视频')
    fps = value.get('fps', 24)
    boundaries = {0, duration_ms}
    for row in rows:
        for second in (row['start'], row['end']):
            point = frame_snap_ms(second * 1000, fps)
            if 0 < point < duration_ms:
                boundaries.add(point)
    boundaries = sorted(boundaries)
    clips = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        source_id = identifiers[index % len(identifiers)] if identifiers else ''
        clips.extend(_fill_clips(value, source_id, start, end - start))
    value['clips'], value['concert'] = clips, {}
    if default_transition != 'cut':
        for index in range(1, len(clips)):
            previous, current = clips[index - 1], clips[index]
            if _frame_aligned(current['startMs'], fps):
                length = frame_snap_ms(min(300, previous['durationMs'] / 2, current['durationMs'] / 2), fps, 'floor')
                if length:
                    current['transition'] = {'id': default_transition, 'durationMs': length}
    return validate_composition(value, duration_ms)


def build_concert_composition(composition, source_id, source_segments, target_segments,
                              duration_ms, gap_source_id='', reviewed=False):
    """Materialize a complete candidate timeline, preserving other editorial tracks."""
    value = validate_composition(composition, duration_ms)
    _bool(reviewed, '人工复核')
    if any(row['locked'] for row in value['clips']):
        raise ValueError('演唱会计划替换前请解锁已有画面片段')
    source = value['sources'].get(source_id)
    if not source or source['kind'] != 'video':
        raise ValueError('请选择已导入的演唱会视频')
    from worker.concert import plan_concert_ranges
    plan = plan_concert_ranges(source_segments, target_segments, source['durationMs'], duration_ms)
    clips = []
    for row in plan['ranges']:
        options = {'purpose': row['purpose'], 'reviewed': reviewed, 'reason': row['reason']}
        if row['purpose'] == 'paired':
            clips.append(make_clip(source_id, 'video', row['startMs'], row['durationMs'],
                                   sourceInMs=row['sourceInMs'], rate=row['rate'], **options))
        else:
            clips.extend(_fill_clips(value, gap_source_id, row['startMs'], row['durationMs'], **options))
    metadata = {key: copy.deepcopy(value['concert'][key]) for key in ('lyricsPath', 'lyricsIdentity')
                if key in value['concert']}
    value['clips'] = clips
    value['concert'] = {key: copy.deepcopy(plan[key]) for key in
                        ('sourceSegments', 'targetDigest', 'pairing', 'repeatedCandidates',
                         'candidateOnly', 'acousticTimingVerified', 'visualSafetyVerified')}
    value['concert'].update(sourceId=source_id, reviewed=reviewed, coverage={})
    value['concert'].update(metadata)
    value['concert']['gapSourceId'] = gap_source_id
    _refresh_concert(value, duration_ms)
    return validate_composition(value, duration_ms)
