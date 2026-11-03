"""Original text-candidate concert planning; no media or network operations.

The dynamic-programming matcher supplies ordered lyric candidates. An exact
repeated chorus may reuse a real source range. These are editorial suggestions,
never a claim of verified vocal synchronization or safe concert footage.
"""

import copy
import hashlib
import json
import math
import unicodedata

from app.studio_tools import map_concert_segments, suggest_concert_pairing, validate_segments


def _positive(value, label):
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value <= 0:
        raise ValueError(label + ' 必须是有限正数')
    return float(value)


def _normalized(text):
    return ''.join(char.casefold() for char in unicodedata.normalize('NFKC', text) if char.isalnum())


def plan_concert_ranges(source_segments, target_segments, source_duration_ms,
                        duration_ms, minimum_rate=.5, maximum_rate=2.0):
    """Return an ordered, complete millisecond plan with bounded source ranges.

    Fill ranges have purpose gap/cutaway and no source pointer; the composition
    layer chooses a selected filler or color. Input subtitle clocks are seconds.
    Only exact unmatched repeated lyrics are reused; approximate or unmatched
    passages remain cutaways. Every suggestion still needs human review.
    """
    source_duration_ms = _positive(source_duration_ms, '演唱会素材时长')
    duration_ms = _positive(duration_ms, '主歌曲时长')
    low, high = _positive(minimum_rate, '最低速度'), _positive(maximum_rate, '最高速度')
    if not .25 <= low <= high <= 4:
        raise ValueError('候选变速范围必须在 0.25–4 倍之间')
    source = validate_segments(source_segments, duration=source_duration_ms / 1000)
    target = validate_segments(target_segments, duration=duration_ms / 1000)
    if not source or not target:
        raise ValueError('演唱会候选需要两侧非空歌词')
    pairing = suggest_concert_pairing(source, target)
    mapped = map_concert_segments(pairing, minimum_rate=low, maximum_rate=high)
    exact_ranges = {}
    for index in range(len(source)):
        for span in range(1, min(3, len(source) - index) + 1):
            indices = list(range(index, index + span))
            key = _normalized(''.join(source[item]['text'] for item in indices))
            if key:
                exact_ranges.setdefault(key, []).append(indices)
    ranges, cursor, repeated = [], 0.0, []

    def fill(start, end, purpose, reason, target_indices=None):
        if end > start:
            ranges.append({'startMs': start, 'durationMs': end - start, 'purpose': purpose,
                           'reason': reason, 'targetIndices': target_indices or []})

    for candidate in mapped:
        start = candidate['timelineStart'] * 1000
        end = start + candidate['timelineDuration'] * 1000
        if start < cursor - 1e-6 or end > duration_ms + 1e-6:
            raise ValueError('目标歌词时间范围重叠或超出主歌曲')
        fill(cursor, start, 'gap', '无目标歌词的前奏、间奏或尾奏')
        indices = candidate.get('sourceIndices', [])
        source_start, source_end, rate = (candidate.get('sourceStart'),
                                         candidate.get('sourceEnd'), candidate.get('playbackRate'))
        reason = '文本顺序候选；需要核对歌唱时码与画面'
        if not indices:
            target_indices = candidate['targetIndices']
            key = _normalized(''.join(target[item]['text'] for item in target_indices))
            alternatives = exact_ranges.get(key, [])
            usable = []
            for alternate in alternatives:
                alternate_start = source[alternate[0]]['start']
                alternate_end = source[alternate[-1]]['end']
                alternate_rate = (alternate_end - alternate_start) * 1000 / (end - start)
                if low <= alternate_rate <= high:
                    usable.append((abs(math.log(alternate_rate)), alternate[0], alternate,
                                   alternate_start, alternate_end, alternate_rate))
            if usable:
                _, _, indices, source_start, source_end, rate = min(usable)
                repeated.append({'targetIndices': target_indices, 'sourceIndices': indices,
                                 'alternatives': len(usable), 'reviewRequired': True})
                reason = '重复歌词复用实际源片段；需要核对副歌版本和画面'
                if len(usable) > 1:
                    reason += '；存在多个文本相同候选'
        if indices and low <= rate <= high:
            source_in = source_start * 1000
            if source_in < 0 or source_in + (end - start) * rate > source_duration_ms + 1e-6:
                raise ValueError('演唱会候选超出视频素材时长')
            ranges.append({'startMs': start, 'durationMs': end - start, 'purpose': 'paired',
                           'sourceInMs': source_in, 'rate': rate, 'reason': reason,
                           'sourceIndices': list(indices), 'targetIndices': candidate['targetIndices']})
        else:
            reason = ('所需速度超出候选范围，请补镜或手动调整'
                      if indices else '未找到足够可靠的歌词对应，使用补镜')
            fill(start, end, 'cutaway', reason, candidate['targetIndices'])
        cursor = end
    fill(cursor, duration_ms, 'gap', '无目标歌词的前奏、间奏或尾奏')
    digest = hashlib.sha256(json.dumps(target, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode('utf-8')).hexdigest()
    return {'ranges': ranges, 'sourceSegments': source, 'targetSegments': target,
            'targetDigest': digest, 'pairing': copy.deepcopy(pairing),
            'repeatedCandidates': repeated, 'candidateOnly': True,
            'acousticTimingVerified': False, 'visualSafetyVerified': False}


def concert_coverage(clips, duration_ms):
    """Measure occupied output time, independently from candidate confidence."""
    duration_ms = _positive(duration_ms, '主歌曲时长')
    intervals, totals = [], {'paired': 0.0, 'cutaway': 0.0, 'gap': 0.0}
    pending = 0
    for clip in clips:
        start, length = clip.get('startMs'), clip.get('durationMs')
        if (isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start)
                or start < 0 or _positive(length, '片段时长') + start > duration_ms + 1e-6):
            raise ValueError('覆盖统计中的片段时间无效')
        purpose = clip.get('purpose', 'gap')
        if purpose not in totals or type(clip.get('reviewed', False)) is not bool:
            raise ValueError('覆盖统计中的候选状态无效')
        intervals.append((start, start + length))
        totals[purpose] += length
        pending += not clip.get('reviewed', False)
    covered, cursor = 0.0, 0.0
    for start, end in sorted(intervals):
        if start < cursor - 1e-6:
            raise ValueError('覆盖统计中的片段重叠')
        covered += end - start
        cursor = end
    full = abs(covered - duration_ms) < 1e-6 and bool(intervals)
    reviewed = full and pending == 0
    return {'totalMs': duration_ms, 'coveredMs': covered, 'pairedMs': totals['paired'],
            'cutawayMs': totals['cutaway'], 'gapMs': totals['gap'],
            'coverageRatio': covered / duration_ms, 'reviewRequiredRows': pending,
            'reviewed': reviewed, 'fullCoverage': full}
