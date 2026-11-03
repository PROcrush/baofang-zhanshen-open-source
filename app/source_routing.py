"""Pure video-fader routing; it never infers which audio vocal is audible.

Source readiness is independent from the automatic side's hysteresis history.
An unready selected deck hides lyrics while retaining that side. Callers reset
the automatic side when the producer epoch changes. A prepared timeline may
still have intentional lyric gaps; the lyric projector handles those gaps.
"""
import math

from app.storage import validate_timeline


LEFT_THRESHOLD = .48
RIGHT_THRESHOLD = .52
MAX_TTL_NS = 1_000_000_000
MAX_INT64 = (1 << 63) - 1
MAX_UNKNOWN_POSITION_MS = 86_400_000


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stamp(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_INT64


def check_source_snapshot(deck, timeline, now_ns, *, clock_status=None, identity_ready=False):
    """Return a diagnostic readiness dict without I/O or clock extrapolation.

    identity_ready is an explicit caller attestation for the current version;
    it defaults to False. clock_status must come from the native estimator.
    Both must be renewed for a new track; a timeline with an old version never
    satisfies the new track. Snapshot timestamps use one monotonic clock.
    """
    version = deck.get('versionId') if isinstance(deck, dict) else None

    def result(ready, code, reason):
        return {'ready': ready, 'reasonCode': code, 'reason': reason,
                'versionId': version if isinstance(version, str) else None,
                'clockStatus': clock_status if isinstance(clock_status, int) and not isinstance(clock_status, bool) else None}

    if not isinstance(deck, dict):
        return result(False, 'source_missing', '选中盘尚无来源快照')
    if not isinstance(version, str) or not version.strip():
        return result(False, 'identity_missing', '选中盘的实际文件版本尚未就绪')
    if identity_ready is not True:
        return result(False, 'identity_unverified', '选中盘的当前文件身份尚未核验')
    if isinstance(clock_status, bool) or not isinstance(clock_status, int) or clock_status != 0:
        reasons = {1: ('clock_stale', '选中盘播放观测已过期'),
                   2: ('clock_invalid', '选中盘播放位置无效'),
                   3: ('clock_reverse', '选中盘反向播放尚未支持')}
        known_status = clock_status if isinstance(clock_status, int) and not isinstance(clock_status, bool) else None
        code, reason = reasons.get(known_status, ('clock_unavailable', '选中盘的播放时钟尚不可用'))
        return result(False, code, reason)
    captured, valid_until = deck.get('capturedNs'), deck.get('validUntilNs')
    if (not _stamp(now_ns) or not _stamp(captured) or not _stamp(valid_until)
            or captured <= 0 or valid_until < captured or valid_until - captured > MAX_TTL_NS):
        return result(False, 'clock_invalid', '选中盘播放快照的有效期无效')
    if now_ns < captured:
        return result(False, 'clock_future', '选中盘播放快照来自未来时刻')
    if now_ns > valid_until:
        return result(False, 'clock_stale', '选中盘播放观测已过期')
    duration, position, speed = (_finite(deck.get(name)) for name in ('durationMs', 'positionMs', 'speed'))
    if (duration is None or duration < 0 or position is None or position < 0
            or (duration > 0 and position > duration)
            or (duration == 0 and position > MAX_UNKNOWN_POSITION_MS)
            or speed is None or speed < 0 or not isinstance(deck.get('playing'), bool)):
        return result(False, 'clock_invalid', '选中盘的播放位置或状态无效')
    if not isinstance(timeline, dict):
        return result(False, 'lyrics_missing', '选中盘尚未准备歌词，保持纯视觉')
    if timeline.get('versionId') != version:
        return result(False, 'lyrics_version_mismatch', '选中盘歌词属于另一文件版本，已隐藏')
    try:
        # Serato's raw length field has an unconfirmed unit. A zero duration
        # means unknown, never a guessed conversion; the verified file version
        # still binds a timeline whose own positive duration is validated.
        validate_timeline(timeline, version, duration if duration > 0 else None)
    except (ValueError, TypeError, KeyError, OverflowError):
        return result(False, 'lyrics_invalid', '选中盘歌词时间轴尚未通过校验')
    return result(True, 'ready', '选中盘的身份、时钟和歌词均已就绪')


def select_lyric_source(selected_source, video_fader, previous_side, sources, *,
                        video_fader_ready=True, left_source='A', right_source='B'):
    """Choose A/B/none and return a JSON-friendly selection diagnostic.

    Feed result['autoSide'] back as previous_side. It tracks the video route
    even during manual override. left/right_source permits reversed Video
    mapping. Manual selection ignores unavailable video-fader data. Exactly
    .48 and .52 belong to the hold band; the first centered frame has no side.
    """
    previous = previous_side if previous_side in ('A', 'B') else 'none'
    fader = _finite(video_fader)
    valid_mapping = (left_source in ('A', 'B') and right_source in ('A', 'B')
                     and left_source != right_source)
    fader_available = video_fader_ready is True and fader is not None and 0 <= fader <= 1 and valid_mapping
    auto_side = previous
    if fader_available:
        if fader < LEFT_THRESHOLD:
            auto_side = left_source
        elif fader > RIGHT_THRESHOLD:
            auto_side = right_source

    basis = 'manual' if selected_source in ('A', 'B') else 'video-fader'

    def output(requested, effective, code, reason):
        return {'requestedSource': requested, 'effectiveSource': effective,
                'autoSide': auto_side, 'reasonCode': code, 'reason': reason,
                'selectionBasis': basis,
                'diagnosticSource': f'{basis}:{requested}',
                'videoFader': fader if fader_available else None,
                'audioVocalSource': 'unverified'}

    if selected_source == 'none':
        basis = 'hidden'
        return output('none', 'none', 'manual_hidden', '已人工隐藏歌词')
    if selected_source not in ('auto', 'A', 'B'):
        basis = 'unavailable'
        return output('none', 'none', 'selection_unavailable', '歌词来源选项不可用，已隐藏')
    if selected_source == 'auto':
        if not fader_available:
            return output('none', 'none', 'video_route_unavailable', '视频推子或路由不可用，自动歌词已隐藏')
        requested = auto_side
        if requested == 'none':
            return output('none', 'none', 'center_without_history', '视频推子居中且尚无上一个来源，已隐藏')
    else:
        requested = selected_source
    source = sources.get(requested) if isinstance(sources, dict) else None
    if not isinstance(source, dict) or source.get('ready') is not True:
        code = source.get('reasonCode') if isinstance(source, dict) else None
        reason = source.get('reason') if isinstance(source, dict) else None
        return output(requested, 'none', code if isinstance(code, str) else 'source_unavailable',
                      reason if isinstance(reason, str) else '选中盘尚未就绪，已隐藏歌词')
    reason = '人工选择的歌词来源已就绪' if basis == 'manual' else '按视频推子选择歌词；音频人声来源未核验'
    return output(requested, requested, 'selected', reason)
