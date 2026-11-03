"""Independent local authoring tools; public subtitle times are seconds.

No original application binaries, catalogs, credentials, or network services
are used. Text correction reuses this project's own conservative correction
module. Concert matching produces review candidates, not certified lip sync.
"""

import copy
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from functools import lru_cache
from pathlib import Path

from worker.correction import correct_with_reference
from worker.subtitle_export import export_lrc, export_srt


MAX_SUBTITLE_BYTES = 32 * 1024 * 1024
TEXT_LAYOUTS = [
    {'id': 'lower-third', 'label': '下方居中', 'x': 960, 'y': 900, 'anchor': 'center'},
    {'id': 'center', 'label': '画面居中', 'x': 960, 'y': 540, 'anchor': 'center'},
    {'id': 'top', 'label': '上方居中', 'x': 960, 'y': 180, 'anchor': 'center'},
    {'id': 'left', 'label': '左侧标题', 'x': 192, 'y': 540, 'anchor': 'left'},
    {'id': 'right', 'label': '右侧标题', 'x': 1728, 'y': 540, 'anchor': 'right'},
]
MEDIA_EXTENSIONS = {
    'audio': {'.wav', '.mp3', '.flac', '.m4a', '.aac', '.aiff', '.aif', '.ogg'},
    'video': {'.mp4', '.mov', '.mkv', '.webm', '.m4v'},
    'image': {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff'},
    'subtitle': {'.srt', '.lrc'},
}


def _number(value, label, minimum=0):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum):
        raise ValueError(label + '必须是有限且有效的数字')
    return float(value)


def _text(value, label='字幕', maximum=500, allow_whitespace=False):
    if (not isinstance(value, str) or not value
            or (not allow_whitespace and not value.strip()) or len(value) > maximum):
        raise ValueError(label + '文本为空或过长')
    if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
        raise ValueError(label + '包含不支持的控制字符')
    return value


def validate_segments(segments, duration=None):
    """Return a validated copy, refusing overlaps or fabricated token clocks."""
    if not isinstance(segments, list) or len(segments) > 10000:
        raise ValueError('字幕片段列表无效或过长')
    limit = _number(duration, '音频时长') if duration is not None else None
    clean, previous_end = [], 0.0
    for row in segments:
        if not isinstance(row, dict):
            raise ValueError('字幕片段必须是对象')
        start, end = _number(row.get('start'), '字幕起点'), _number(row.get('end'), '字幕终点')
        if start < previous_end or end <= start or (limit is not None and end > limit + 1e-9):
            raise ValueError('字幕时间轴重叠、倒序或超出音频范围')
        result = copy.deepcopy(row)
        result.update(start=start, end=end, text=_text(row.get('text')))
        if 'characters' in row:
            if not isinstance(row['characters'], list) or len(row['characters']) > 500:
                raise ValueError('逐字或词时码格式无效')
            last, joined, tokens = start, '', []
            for token in row['characters']:
                if not isinstance(token, dict):
                    raise ValueError('逐字或词时码必须是对象')
                ts, te = _number(token.get('start'), '字词起点'), _number(token.get('end'), '字词终点')
                if ts < last or te <= ts or te > end + 1e-9:
                    raise ValueError('逐字或词时码重叠或超出句范围')
                text = _text(token.get('text'), '字词', allow_whitespace=True)
                tokens.append({**copy.deepcopy(token), 'text': text, 'start': ts, 'end': te})
                joined += text
                last = te
            if tokens and joined != result['text']:
                raise ValueError('逐字或词文本与完整字幕不一致')
            if tokens:
                result['characters'] = tokens
            else:
                result.pop('characters', None)
        clean.append(result)
        previous_end = end
    return clean


def _timeline(segments):
    """Adapt seconds to the repository's own validated millisecond protocol."""
    rows = validate_segments(segments)
    if not rows:
        raise ValueError('字幕列表为空')
    word_quality = all(row.get('characters') for row in rows)
    normalized = []
    for row in rows:
        item = {'startMs': row['start'] * 1000, 'endMs': row['end'] * 1000, 'text': row['text']}
        if word_quality:
            item['words'] = [{'startMs': token['start'] * 1000, 'endMs': token['end'] * 1000,
                              'text': token['text']} for token in row['characters']]
        normalized.append(item)
    identity = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                                         allow_nan=False).encode('utf-8')).hexdigest()
    return {'schemaVersion': 1, 'versionId': 'sha256:' + identity,
            'durationMs': rows[-1]['end'] * 1000,
            'quality': 'word' if word_quality else 'sentence',
            'timingProvenance': 'imported_token' if word_quality else 'imported_sentence',
            'acousticAccuracyVerified': False, 'segments': normalized}


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 包含重复字段：' + key)
        result[key] = value
    return result


def _read_json(content):
    def invalid_constant(value):
        raise ValueError('JSON 含非有限数字：' + value)
    return json.loads(content, object_pairs_hook=_json_object, parse_constant=invalid_constant)


def _srt_seconds(value):
    match = re.fullmatch(r'(\d{1,3}):([0-5]\d):([0-5]\d)[,.](\d{3})', value)
    if not match:
        raise ValueError('SRT 时间格式无效')
    hour, minute, second, fraction = (int(part) for part in match.groups())
    return hour * 3600 + minute * 60 + second + fraction / 1000


def _parse_srt(content):
    rows = []
    for block in re.split(r'\n[ \t]*\n', content.strip()):
        lines = block.strip().splitlines()
        if lines and lines[0].strip().isdigit():
            lines.pop(0)
        if not lines:
            continue
        clock = re.fullmatch(r'(\S+)\s*-->\s*(\S+)(?:[ \t]+.*)?', lines[0].strip())
        if not clock or len(lines) < 2:
            raise ValueError('SRT 中存在无效字幕块')
        rows.append({'start': _srt_seconds(clock[1]), 'end': _srt_seconds(clock[2]),
                     'text': '\n'.join(lines[1:]).strip(), 'timingQuality': 'sentence'})
    return rows


_LRC_CLOCK = r'(\d{1,4}):([0-5]\d)(?:[.:](\d{1,3}))?'


def _lrc_seconds(match):
    minute, second, fraction = match.groups()
    return int(minute) * 60 + int(second) + (int(fraction) / 10 ** len(fraction) if fraction else 0)


def _parse_lrc(content, duration):
    events, offset = [], 0.0
    for line in content.splitlines():
        match = re.fullmatch(r'\s*\[offset:([+-]?\d+)\]\s*', line, re.I)
        if match:
            offset = int(match[1]) / 1000
    for line in content.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r'\[(ar|al|ti|au|by|re|ve|la|length|offset):[^\]]*\]', line, re.I):
            continue
        tags, position = [], 0
        while True:
            match = re.compile(r'\[' + _LRC_CLOCK + r'\]').match(line, position)
            if not match:
                break
            tags.append(_lrc_seconds(match) + offset)
            position = match.end()
        if not tags:
            raise ValueError('LRC 中存在没有有效时间戳的歌词行')
        text = line[position:]
        enhanced = list(re.finditer(r'<' + _LRC_CLOCK + r'>', text))
        if enhanced and len(tags) != 1:
            raise ValueError('增强 LRC 的字词时码不能同时绑定多个句起点')
        for timestamp in tags:
            events.append({'start': timestamp, 'raw': text, 'enhanced': enhanced})
    events.sort(key=lambda event: event['start'])
    if any(event['start'] < 0 for event in events):
        raise ValueError('LRC offset 使歌词起点变为负数')
    if any(a['start'] == b['start'] for a, b in zip(events, events[1:])):
        raise ValueError('LRC 存在重复时间戳，需先确认对应歌词')
    rows = []
    for index, event in enumerate(events):
        if not event['raw'].strip():  # Explicit blank end marker.
            continue
        end = events[index + 1]['start'] if index + 1 < len(events) else (
            duration if duration is not None else event['start'] + 5)
        row = {'start': event['start'], 'end': end, 'text': event['raw'], 'timingQuality': 'sentence'}
        enhanced = event['enhanced']
        if enhanced:
            pieces = []
            prefix = event['raw'][:enhanced[0].start()]
            if prefix:
                pieces.append({'text': prefix, 'start': event['start'],
                               'end': _lrc_seconds(enhanced[0]) + offset})
            for token_index, token in enumerate(enhanced):
                token_end = enhanced[token_index + 1].start() if token_index + 1 < len(enhanced) else len(event['raw'])
                text = event['raw'][token.end():token_end]
                if not text:
                    raise ValueError('增强 LRC 存在没有文字的字词时间戳')
                pieces.append({'text': text, 'start': _lrc_seconds(token) + offset,
                               'end': _lrc_seconds(enhanced[token_index + 1]) + offset
                               if token_index + 1 < len(enhanced) else end})
            row.update(text=''.join(piece['text'] for piece in pieces), characters=pieces,
                       timingQuality='provided')
        rows.append(row)
    return rows


def _parse_subtitle_json(data):
    if isinstance(data, list) or (isinstance(data, dict) and isinstance(data.get('segments'), list)):
        rows = data if isinstance(data, list) else data['segments']
        clean = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('JSON 字幕片段格式无效')
            if 'startMs' in row or 'endMs' in row:
                row = {**row, 'start': _number(row.get('startMs'), '字幕起点') / 1000,
                       'end': _number(row.get('endMs'), '字幕终点') / 1000}
                row.pop('startMs', None)
                row.pop('endMs', None)
                supplied_tokens = row.pop('words', None) if 'characters' not in row else row['characters']
                if supplied_tokens is not None:
                    if not isinstance(supplied_tokens, list):
                        raise ValueError('JSON 字词时码必须是数组')
                    converted_tokens = []
                    for token in supplied_tokens:
                        converted = {**token, 'start': _number(token.get('startMs'), '字词起点') / 1000,
                                     'end': _number(token.get('endMs'), '字词终点') / 1000}
                        converted.pop('startMs', None)
                        converted.pop('endMs', None)
                        converted_tokens.append(converted)
                    row['characters'] = converted_tokens
            clean.append(row)
        return clean
    if not isinstance(data, dict) or not isinstance(data.get('tracks'), list):
        raise ValueError('JSON 不包含字幕 segments 或剪映字幕轨道')
    materials = data.get('materials', {}).get('texts', [])
    if not isinstance(materials, list):
        raise ValueError('剪映文字素材格式无效')
    by_id = {item['id']: item for item in materials if isinstance(item, dict) and 'id' in item}
    rows = []
    for track in data['tracks']:
        if not isinstance(track, dict) or track.get('type') != 'text':
            continue
        for segment in track.get('segments', []):
            material = by_id.get(segment.get('material_id'))
            if not material or not (material.get('is_auto_caption') or material.get('type') == 'subtitle'
                                    or '字幕' in track.get('name', '')):
                continue
            body = material.get('content')
            body = _read_json(body) if isinstance(body, str) else body
            text = body.get('text') if isinstance(body, dict) else None
            clock = segment.get('target_timerange', {})
            start = _number(clock.get('start'), '剪映字幕起点') / 1_000_000
            length = _number(clock.get('duration'), '剪映字幕时长') / 1_000_000
            rows.append({'start': start, 'end': start + length, 'text': text, 'timingQuality': 'sentence'})
    return sorted(rows, key=lambda row: row['start'])


def load_subtitles(path, duration=None):
    """Read an explicitly selected SRT, LRC, or JSON; never infer word timing."""
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_SUBTITLE_BYTES:
        raise ValueError('字幕文件过大或不是普通文件')
    content = source.read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    if duration is not None:
        duration = _number(duration, '音频时长')
    suffix = source.suffix.casefold()
    if suffix == '.srt':
        rows = _parse_srt(content)
    elif suffix == '.lrc':
        rows = _parse_lrc(content, duration)
    elif suffix == '.json':
        try:
            rows = _parse_subtitle_json(_read_json(content))
        except (TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise ValueError('JSON 字幕结构无效') from error
    else:
        raise ValueError('请选择 SRT、LRC 或 JSON 字幕')
    rows = validate_segments(rows, duration)
    if not rows:
        raise ValueError('文件中没有有效字幕')
    notice = '保留文件提供的句级或字词时码，未进行声学重对齐。'
    if suffix == '.lrc':
        notice += '普通 LRC 终点取下一时间戳；缺少末句终点和音频时长时默认显示 5 秒。'
    return {'schema': 'baofang-subtitles/v1', 'segments': rows, 'sourceFormat': suffix[1:].upper(),
            'timingRealigned': False, 'acousticAccuracyVerified': False,
            'notice': notice}


def export_subtitles(segments, format, destination=None):
    """Serialize SRT/LRC/JSON, optionally creating a new UTF-8 file only."""
    if not isinstance(format, str):
        raise ValueError('字幕导出格式无效')
    format = format.casefold()
    rows = validate_segments(segments)
    if format == 'json':
        content = json.dumps({'schema': 'baofang-subtitles/v1', 'segments': rows},
                             ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    elif format in ('srt', 'lrc'):
        content = (export_srt if format == 'srt' else export_lrc)(_timeline(rows))
    else:
        raise ValueError('字幕导出格式必须为 srt、lrc 或 json')
    if destination is not None:
        target = Path(destination).expanduser().absolute()
        with target.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(content)
    return content


def correct_reference(segments, reference):
    """Correct against explicit user text using our existing audited matcher."""
    rows = validate_segments(segments)
    corrected = correct_with_reference(_timeline(rows), reference)
    output = copy.deepcopy(rows)
    for result, original, corrected_row in zip(output, rows, corrected['segments']):
        result['text'] = corrected_row['text']
        if original.get('characters') and len(original['text']) == len(result['text']):
            offset = 0
            for token in result['characters']:
                length = len(token['text'])
                token['text'] = result['text'][offset:offset + length]
                offset += length
        elif original.get('characters'):
            result.pop('characters', None)
            result['timingQuality'] = 'sentence'
    changes = [{'index': change['segmentIndex'], 'before': change['originalText'],
                'after': change['correctedText'], 'confidence': change['similarity'],
                'timingAction': change['timingAction']} for change in corrected['correction']['changes']]
    review = corrected['correction']['review']
    return {'segments': validate_segments(output), 'changes': changes, 'review': review,
            'stats': {'changedLines': len(changes), 'unchangedLines': len(rows) - len(changes),
                      'unmatchedLines': len(review)}, 'timingRealigned': False,
            'acousticAccuracyVerified': False}


def split_segment(segments, index, split_time, text_index=None):
    """Split one cue; optional text_index states the user's exact text boundary."""
    rows = validate_segments(segments)
    if type(index) is not int or not 0 <= index < len(rows):
        raise ValueError('要拆分的字幕不存在')
    row = rows[index]
    split_time = _number(split_time, '拆分时间')
    if not row['start'] < split_time < row['end'] or len(row['text']) < 2:
        raise ValueError('拆分点必须在字幕内部，且两边都有文字')
    inferred = text_index is None
    if inferred:
        text_index = max(1, min(len(row['text']) - 1, round(len(row['text']) *
                         (split_time - row['start']) / (row['end'] - row['start']))))
    if type(text_index) is not int or not 0 < text_index < len(row['text']):
        raise ValueError('文字拆分点无效')
    left, right = copy.deepcopy(row), copy.deepcopy(row)
    left.update(end=split_time, text=row['text'][:text_index])
    right.update(start=split_time, text=row['text'][text_index:])
    for side, suffix in [(left, '1'), (right, '2')]:
        if 'id' in side:
            side['id'] = str(side['id']) + '/' + suffix
        side.pop('characters', None)
        side['splitTextBoundary'] = 'time_proportion' if inferred else 'user_selected'
        side['timingQuality'] = 'sentence'
    tokens = row.get('characters', [])
    cut, character_count = 0, 0
    for token in tokens:
        if character_count < text_index:
            cut += 1
            character_count += len(token['text'])
    if tokens and character_count == text_index and 0 < cut < len(tokens):
        if tokens[cut - 1]['end'] <= split_time <= tokens[cut]['start']:
            left.update(characters=tokens[:cut], timingQuality='provided')
            right.update(characters=tokens[cut:], timingQuality='provided')
    return validate_segments(rows[:index] + [left, right] + rows[index + 1:])


def merge_segments(segments, indices, separator=''):
    """Merge consecutive cues without moving their supplied token intervals."""
    rows = validate_segments(segments)
    if (not isinstance(indices, (list, tuple)) or len(indices) < 2
            or any(type(index) is not int for index in indices)
            or list(indices) != list(range(indices[0], indices[-1] + 1))
            or indices[0] < 0 or indices[-1] >= len(rows)):
        raise ValueError('合并只能选择至少两条连续字幕')
    if not isinstance(separator, str) or len(separator) > 2:
        raise ValueError('合并分隔符无效')
    selected = rows[indices[0]:indices[-1] + 1]
    result = copy.deepcopy(selected[0])
    result.update(end=selected[-1]['end'], text=separator.join(row['text'] for row in selected))
    result.pop('characters', None)
    result['timingQuality'] = 'sentence'
    if not separator and all(row.get('characters') for row in selected):
        result['characters'] = [token for row in selected for token in row['characters']]
        result['timingQuality'] = 'provided'
    return validate_segments(rows[:indices[0]] + [result] + rows[indices[-1] + 1:])


def make_text_clip(text, start, duration, layout='lower-third', **style):
    """Create one of five original 1920x1080 text layouts for preview/export."""
    selected = next((row for row in TEXT_LAYOUTS if row['id'] == layout), None)
    if selected is None:
        raise ValueError('独立文本布局不存在')
    clip = {'kind': 'text', 'text': text, 'timelineStart': start, 'timelineDuration': duration,
            'layout': layout, 'x': selected['x'], 'y': selected['y'], 'anchor': selected['anchor'],
            'fontSize': 56, 'fontFamily': 'PingFang SC', 'fontBold': False,
            'color': '#FFFFFF', 'opacity': 1.0, **style}
    identity = hashlib.sha256(json.dumps(clip, ensure_ascii=False, sort_keys=True,
                                         allow_nan=False).encode('utf-8')).hexdigest()[:16]
    clip.setdefault('id', 'text-' + identity)
    return validate_text_clips([clip])[0]


def validate_text_clips(clips, duration=None):
    if not isinstance(clips, list) or len(clips) > 200:
        raise ValueError('独立文本最多支持 200 段')
    clean, ids, events = [], set(), []
    for clip in clips:
        if not isinstance(clip, dict) or clip.get('kind', 'text') != 'text':
            raise ValueError('独立文本片段格式无效')
        result = copy.deepcopy(clip)
        result['text'] = _text(clip.get('text'), '独立文本', 120)
        if len(result['text'].split('\n')) > 8:
            raise ValueError('独立文本最多支持 8 行')
        start = _number(clip.get('timelineStart'), '文本起点')
        length = _number(clip.get('timelineDuration'), '文本时长')
        if length <= 0 or (duration is not None and start + length > _number(duration, '时长') + 1e-9):
            raise ValueError('独立文本时间超出范围')
        for field, maximum in [('x', 1920), ('y', 1080), ('fontSize', 256), ('opacity', 1)]:
            value = _number(clip.get(field, 1 if field == 'opacity' else 56), field)
            if value > maximum or (field == 'fontSize' and value < 8):
                raise ValueError('独立文本 ' + field + ' 超出范围')
            result[field] = value
        color = clip.get('color', '#FFFFFF')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('独立文本颜色必须为 #RRGGBB')
        if clip.get('anchor', 'center') not in ('left', 'center', 'right'):
            raise ValueError('独立文本对齐方式无效')
        if type(clip.get('fontBold', False)) is not bool:
            raise ValueError('独立文本粗体参数无效')
        if not isinstance(clip.get('fontFamily', 'PingFang SC'), str) or len(clip.get('fontFamily', '')) > 128:
            raise ValueError('独立文本字体名称无效')
        identifier = clip.get('id')
        if identifier is not None:
            if not isinstance(identifier, str) or not identifier or identifier in ids:
                raise ValueError('独立文本标识为空或重复')
            ids.add(identifier)
        result.update(timelineStart=start, timelineDuration=length)
        events.extend([(start, 1), (start + length, -1)])
        clean.append(result)
    active = 0
    for _, change in sorted(events):  # Ends sort before starts at equal times.
        active += change
        if active > 12:
            raise ValueError('同一时刻最多显示 12 段独立文本')
    return clean


def _ffmpeg_path(path=None):
    selected = path or shutil.which('ffmpeg')
    if not selected:
        raise ValueError('系统 FFmpeg 尚未安装')
    resolved = Path(selected).expanduser().resolve(strict=True)
    from app.runtime_paths import is_packaged_ffmpeg
    if any(part.endswith('.app') for part in resolved.parts) and not is_packaged_ffmpeg(resolved):
        raise ValueError('独立工具不能依赖其他应用原包')
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError('FFmpeg 路径不是可执行文件')
    return resolved


def build_delogo_command(video, output, roi, ffmpeg_path=None, preview_start=None,
                         preview_duration=5.0, frame_size=None):
    """Build a local FFmpeg delogo command. ROI values are integer pixels."""
    executable = _ffmpeg_path(ffmpeg_path)
    source = Path(video).expanduser().resolve(strict=True)
    target = Path(output).expanduser().absolute()
    if not source.is_file() or target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError('去字幕需要有效源视频和全新的输出路径')
    if target.suffix.casefold() != '.mp4':
        raise ValueError('去字幕输出必须为 MP4')
    if isinstance(roi, dict):
        values = [roi.get(key) for key in ('x', 'y', 'width', 'height')]
    else:
        values = list(roi) if isinstance(roi, (list, tuple)) else []
    if len(values) != 4 or any(type(value) is not int for value in values):
        raise ValueError('去字幕区域必须是四个整数像素坐标')
    x, y, width, height = values
    if min(x, y) < 1 or min(width, height) < 2:
        raise ValueError('去字幕区域须至少 2 像素并留出画面边缘')
    if frame_size is not None:
        if (not isinstance(frame_size, (list, tuple)) or len(frame_size) != 2
                or any(type(value) is not int or value <= 0 for value in frame_size)):
            raise ValueError('视频尺寸无效')
        if x + width >= frame_size[0] or y + height >= frame_size[1]:
            raise ValueError('去字幕区域超出视频边缘')
    command = [str(executable), '-nostdin', '-hide_banner', '-loglevel', 'error', '-n']
    if preview_start is not None:
        command += ['-ss', str(_number(preview_start, '预览起点'))]
    command += ['-i', str(source)]
    if preview_start is not None:
        length = _number(preview_duration, '预览时长')
        if not 0 < length <= 5:
            raise ValueError('去字幕预览时长必须在 0–5 秒')
        command += ['-t', str(length)]
    return command + ['-vf', f'delogo=x={x}:y={y}:w={width}:h={height}:show=0',
                      '-map', '0:v:0', '-map', '0:a?', '-c:v', 'libx264', '-preset', 'medium',
                      '-crf', '18', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(target)]


def remove_subtitles(video, output, roi, ffmpeg_path=None, **options):
    """Run delogo into private staging and publish a new file without replacement."""
    target = Path(output).expanduser().absolute()
    # Validate the chosen output before creating staging or starting a process.
    build_delogo_command(video, target, roi, ffmpeg_path, **options)
    with tempfile.TemporaryDirectory(prefix='.baofang-delogo-', dir=target.parent) as directory:
        stage = Path(directory) / 'output.mp4'
        command = build_delogo_command(video, stage, roi, ffmpeg_path, **options)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if completed.returncode or not stage.is_file() or not stage.stat().st_size:
            raise RuntimeError('FFmpeg 去字幕未完成：' + completed.stderr[-2000:])
        os.link(stage, target)  # Exclusive publication; an existing path is never replaced.
    return {'output': str(target), 'method': 'ffmpeg-delogo', 'temporalRecovery': False,
            'notice': '使用周边像素遮盖固定区域；不恢复原始被遮挡画面。'}


def _normalized_lyric(text):
    return ''.join(char.casefold() for char in unicodedata.normalize('NFKC', text) if char.isalnum())


def suggest_concert_pairing(source_segments, target_segments, minimum_similarity=.68, max_span=2):
    """Monotonic dynamic programming with source/target skips and small groups.

    This is our text-only candidate matcher. Repetition, weak matches, picture
    safety, vocals, and acoustic timing still require review before rendering.
    """
    source, target = validate_segments(source_segments), validate_segments(target_segments)
    n, m = len(source), len(target)
    if not n or not m or n * m > 90000:
        raise ValueError('请选择非空歌词范围；一次候选配对最多支持 90,000 个行组合')
    if type(max_span) is not int or not 1 <= max_span <= 3:
        raise ValueError('歌词合并跨度须为 1–3 行')
    threshold = _number(minimum_similarity, '歌词相似度')
    if not .5 <= threshold <= 1:
        raise ValueError('歌词相似度阈值须为 0.5–1')
    def groups(rows):
        return {(index, span): _normalized_lyric(''.join(row['text'] for row in rows[index:index + span]))
                for index in range(len(rows)) for span in range(1, min(max_span, len(rows) - index) + 1)}
    sg, tg = groups(source), groups(target)
    score = [[-math.inf] * (m + 1) for _ in range(n + 1)]
    trace = [[None] * (m + 1) for _ in range(n + 1)]
    score[0][0] = 0
    alternatives_by_target = {}
    @lru_cache(maxsize=8192)
    def text_similarity(left, right):
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
        return matcher.ratio() if matcher.quick_ratio() >= threshold else 0.0
    def update(i, j, value, previous):
        if value > score[i][j] + 1e-10:
            score[i][j], trace[i][j] = value, previous
    for i in range(n + 1):
        for j in range(m + 1):
            current = score[i][j]
            if i < n:
                update(i + 1, j, current - .02, (i, j, 'sourceUnused', 0.0))
            if j < m:
                update(i, j + 1, current - .4, (i, j, 'targetCutaway', 0.0))
            for ss in range(1, min(max_span, n - i) + 1):
                for ts in range(1, min(max_span, m - j) + 1):
                    left, right = sg[i, ss], tg[j, ts]
                    value = text_similarity(left, right)
                    if value >= threshold:
                        alternatives = alternatives_by_target.setdefault((j, ts), [])
                        alternatives.append((value, i, ss))
                        alternatives.sort(reverse=True)
                        del alternatives[2:]
                        update(i + ss, j + ts, current + value * ts - .05 * (ss + ts - 2),
                               (i, j, 'pair', value))
    rows, i, j = [], n, m
    while i or j:
        pi, pj, action, value = trace[i][j]
        si, ti = list(range(pi, i)), list(range(pj, j))
        alternatives = sorted((value, si, ss) for value, si, ss
                              in alternatives_by_target.get((pj, j - pj), [])
                              if action == 'pair' and (si, ss) != (pi, i - pi))
        margin = value - alternatives[-1][0] if alternatives else 1.0
        review = action != 'pair' or value < .9 or margin < .08
        rows.append({'action': action, 'sourceIndices': si, 'targetIndices': ti,
                     'sourceText': ''.join(source[index]['text'] for index in si),
                     'targetText': ''.join(target[index]['text'] for index in ti),
                     'similarity': round(value, 6), 'candidateMargin': round(margin, 6),
                     'reviewRequired': review,
                     'reason': '需要人工复核候选歌词与画面' if review else '文本顺序候选匹配；尚未核验画面'})
        i, j = pi, pj
    rows.reverse()
    return {'schema': 'baofang-concert-pairing/v1', 'candidateOnly': True,
            'sourceSegments': source, 'targetSegments': target, 'rows': rows,
            'summary': {'pairRows': sum(row['action'] == 'pair' for row in rows),
                        'unmatchedTargetLines': sum(len(row['targetIndices']) for row in rows
                                                    if row['action'] == 'targetCutaway'),
                        'reviewRequiredRows': sum(row['reviewRequired'] for row in rows)},
            'acousticTimingVerified': False, 'visualSafetyVerified': False}


def map_concert_segments(pairing, minimum_rate=.5, maximum_rate=2.0):
    """Turn pairing candidates into timed clip suggestions, without touching video."""
    if not isinstance(pairing, dict) or pairing.get('schema') != 'baofang-concert-pairing/v1':
        raise ValueError('演唱会候选配对格式无效')
    source, target = validate_segments(pairing['sourceSegments']), validate_segments(pairing['targetSegments'])
    low, high = _number(minimum_rate, '最低速度'), _number(maximum_rate, '最高速度')
    if not 0 < low <= high:
        raise ValueError('候选画面速度范围无效')
    clips, previous_target = [], -1
    for row in pairing.get('rows', []):
        si, ti = row.get('sourceIndices', []), row.get('targetIndices', [])
        if row.get('action') not in ('pair', 'sourceUnused', 'targetCutaway'):
            raise ValueError('演唱会候选操作无效')
        for indices, count in [(si, len(source)), (ti, len(target))]:
            if (not isinstance(indices, list) or any(type(index) is not int or not 0 <= index < count for index in indices)
                    or (indices and indices != list(range(indices[0], indices[-1] + 1)))):
                raise ValueError('演唱会候选行索引无效')
        if ((row['action'] == 'pair' and (not si or not ti))
                or (row['action'] == 'sourceUnused' and (not si or ti))
                or (row['action'] == 'targetCutaway' and (si or not ti))):
            raise ValueError('演唱会候选操作与两侧歌词不一致')
        if not ti:
            continue
        if ti[0] <= previous_target:
            raise ValueError('演唱会目标歌词重复使用或倒序')
        previous_target = ti[-1]
        start, end = target[ti[0]]['start'], target[ti[-1]]['end']
        clip = {'id': 'concert-' + str(len(clips) + 1), 'timelineStart': start,
                'timelineDuration': end - start, 'targetIndices': ti, 'candidateOnly': True,
                'reviewRequired': True, 'kind': 'cutawayRequired'}
        if row['action'] == 'pair' and si:
            source_start, source_end = source[si[0]]['start'], source[si[-1]]['end']
            rate = (source_end - source_start) / (end - start)
            clip.update(sourceIndices=si, sourceStart=source_start, sourceEnd=source_end, playbackRate=rate)
            if low <= rate <= high:
                clip['kind'] = 'lipSyncCandidate'
            else:
                clip['reason'] = '整句所需速度超出候选范围，请补镜或手动调整'
        clips.append(clip)
    return clips


def index_media_directory(root, recursive=True, max_files=10000):
    """Read only the explicitly selected directory; do not follow symlinks."""
    selected = Path(root).expanduser().resolve(strict=True)
    if not selected.is_dir() or type(max_files) is not int or max_files < 1:
        raise ValueError('素材目录或文件上限无效')
    files, errors, truncated = [], [], False
    def failure(error):
        errors.append({'path': str(error.filename), 'message': str(error)})
    for directory, directories, names in os.walk(selected, followlinks=False, onerror=failure):
        directories[:] = sorted(name for name in directories if not (Path(directory) / name).is_symlink()) if recursive else []
        for name in sorted(names):
            path = Path(directory) / name
            kind = next((kind for kind, extensions in MEDIA_EXTENSIONS.items() if path.suffix.casefold() in extensions), None)
            if kind is None or path.is_symlink():
                continue
            if len(files) >= max_files:
                truncated = True
                break
            try:
                info = path.stat()
                if not path.is_file():
                    continue
                files.append({'path': str(path), 'relativePath': str(path.relative_to(selected)),
                              'kind': kind, 'size': info.st_size, 'mtimeNs': info.st_mtime_ns})
            except OSError as error:
                failure(error)
        if truncated:
            break
    return {'root': str(selected), 'files': files, 'errors': errors, 'truncated': truncated,
            'readOnly': True}


def suggest_duplicate_files(paths):
    """Return content-SHA groups and hard-link aliases; never delete any file."""
    if not isinstance(paths, (list, tuple)) or len(paths) > 10000:
        raise ValueError('去重候选文件列表无效')
    by_size, seen = {}, set()
    for item in paths:
        raw = Path(item['path'] if isinstance(item, dict) else item).expanduser().absolute()
        if raw.is_symlink():
            raise ValueError('去重建议不读取符号链接')
        path = raw.resolve(strict=True)
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            raise ValueError('去重候选必须是普通文件')
        info = path.stat()
        by_size.setdefault(info.st_size, []).append((path, info))
    groups = []
    for size, candidates in by_size.items():
        if len(candidates) < 2:
            continue
        digests = {}
        for path, before in candidates:
            digest = hashlib.sha256()
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(block)
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError('计算摘要期间文件发生变化，请重新选择')
            digests.setdefault(digest.hexdigest(), []).append((str(path), (after.st_dev, after.st_ino)))
        for digest, entries in digests.items():
            if len(entries) > 1:
                entries.sort()
                groups.append({'sha256': digest, 'size': size, 'paths': [path for path, _ in entries],
                               'suggestedKeep': entries[0][0],
                               'physicalFileCount': len({identity for _, identity in entries}),
                               'action': 'review_only', 'deleted': False})
    return sorted(groups, key=lambda group: group['paths'][0])
