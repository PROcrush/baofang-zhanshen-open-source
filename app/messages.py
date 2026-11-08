"""Validate complete IPC snapshots before callers replace live state."""
import math

from app.storage import validate_timeline


MAX_INT64 = (1 << 63) - 1
MAX_TTL_NS = 1_000_000_000
SOURCES = ('auto', 'A', 'B', 'none')
SCENES = ('amber', 'blue', 'black')


def _object(value, name):
    if not isinstance(value, dict):
        raise ValueError(f'{name} 必须是对象')
    return value


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_INT64:
        raise ValueError(f'{name} 必须是有效整数')
    return value


def _number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} 必须是有限数值')
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f'{name} 必须是有限数值') from None
    if not math.isfinite(number) or (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        raise ValueError(f'{name} 超出有效范围')
    return number


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ValueError(f'{name} 必须是布尔值')


def _string(value, name, limit, allow_empty=False):
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise ValueError(f'{name} 文本无效')
    return value


def _deck(value, name, live=False):
    deck = _object(value, name)
    _string(deck.get('versionId'), f'{name}.versionId', 256)
    _string(deck.get('title'), f'{name}.title', 512)
    _string(deck.get('path'), f'{name}.path', 8192, allow_empty=True)
    duration = _number(deck.get('durationMs'), f'{name}.durationMs', 0)
    if duration <= 0 and not live:
        raise ValueError(f'{name}.durationMs 必须大于零')
    _number(deck.get('positionMs'), f'{name}.positionMs', 0, duration if duration else 86_400_000)
    _number(deck.get('speed'), f'{name}.speed', -20 if live else .8, 20 if live else 1.2)
    captured = _integer(deck.get('capturedNs'), f'{name}.capturedNs', 1)
    valid_until = _integer(deck.get('validUntilNs'), f'{name}.validUntilNs', 1)
    if valid_until < captured or valid_until - captured > MAX_TTL_NS:
        raise ValueError(f'{name} 观测有效期无效')
    _boolean(deck.get('playing'), f'{name}.playing')
    _integer(deck.get('generation'), f'{name}.generation')
    if live:
        _boolean(deck.get('identityReady'), f'{name}.identityReady')
        if deck.get('lyricVersionId') is not None:
            _string(deck['lyricVersionId'], f'{name}.lyricVersionId', 256, allow_empty=True)
    return deck


def _decks(value, allow_empty=False, live=False, allow_partial=False):
    decks = _object(value, 'decks')
    if allow_empty and not decks:
        return decks
    if not (set(decks) <= {'A', 'B'} if allow_partial else set(decks) == {'A', 'B'}):
        raise ValueError('播放盘必须恰好包含 A 和 B')
    for key in decks:
        _deck(decks[key], f'deck{key}', live)
        if allow_partial and decks[key].get('clockMode') != 'observed':
            raise ValueError('rekordbox 仅接受直接观测时钟')
    return decks


def validate_replay_snapshot(payload):
    """Return the original snapshot or raise ValueError; never partially mutate."""
    snapshot = _object(payload, '回放消息')
    protocol = snapshot.get('protocolVersion')
    if isinstance(protocol, bool) or not isinstance(protocol, int) or protocol != 1:
        raise ValueError('回放协议版本无效')
    if snapshot.get('mode') != 'replay':
        raise ValueError('消息不是回放数据')
    _string(snapshot.get('epoch'), 'epoch', 128)
    _integer(snapshot.get('sequence'), 'sequence', 1)
    if snapshot.get('activeDeck') not in ('A', 'B'):
        raise ValueError('有效播放盘无效')
    _boolean(snapshot.get('playing'), 'playing')
    _number(snapshot.get('speed'), 'speed', .8, 1.2)
    _boolean(snapshot.get('loopEnabled'), 'loopEnabled')
    _decks(snapshot.get('decks'))
    return payload


def accept_snapshot(previous_epoch, previous_sequence, payload):
    """Validate, then accept a new epoch or increasing sequence in one epoch.

    Malformed payloads raise ValueError. Reordered/duplicate valid snapshots
    return False. Callers clear any old interpolation history on epoch change.
    """
    snapshot = validate_replay_snapshot(payload)
    if snapshot['epoch'] != previous_epoch:
        return True
    _integer(previous_sequence, 'previous_sequence')
    return snapshot['sequence'] > previous_sequence


def validate_serato_frame(payload):
    """Validate a complete read-only frame; positions are original-source seconds."""
    frame = _object(payload, 'Serato 消息')
    if type(frame.get('protocolVersion')) is not int or frame['protocolVersion'] != 1 or frame.get('mode') != 'serato':
        raise ValueError('Serato 协议版本或模式无效')
    if frame.get('type') != 'serato_frame' or frame.get('producerVerified') is not True:
        raise ValueError('生产者身份尚未核验')
    _string(frame.get('epoch'), 'epoch', 128)
    _integer(frame.get('sequence'), 'sequence', 1)
    captured = _integer(frame.get('capturedNs'), 'capturedNs', 1)
    deadline = _integer(frame.get('validUntilNs'), 'validUntilNs', 1)
    if not 0 <= deadline - captured <= 350_000_000:
        raise ValueError('Serato 样本有效期无效')
    decks = frame.get('decks')
    if not isinstance(decks, list) or len(decks) != 2:
        raise ValueError('Serato 必须包含两盘完整状态')
    indices, identities = set(), set()
    for deck in decks:
        _object(deck, 'Serato 播放盘')
        index = _integer(deck.get('deck_index'), 'deck_index')
        if index not in (0, 1) or index in indices:
            raise ValueError('Serato 盘索引重复或无效')
        indices.add(index)
        identity = _integer(deck.get('source_identity'), 'source_identity', 1)
        if identity in identities:
            raise ValueError('Serato 来源身份重复')
        identities.add(identity)
        _string(deck.get('audio_path'), 'audio_path', 8192, allow_empty=True)
        _number(deck.get('position_seconds'), 'position_seconds', 0, 86400)
        _number(deck.get('velocity'), 'velocity', -20, 20)
    fader = _object(frame.get('fader'), 'Serato 视频路由')
    _number(fader.get('normalized'), 'fader.normalized', 0, 1)
    left = _integer(fader.get('left_deck_index'), 'fader.left_deck_index')
    right = _integer(fader.get('right_deck_index'), 'fader.right_deck_index')
    if {left, right} != {0, 1}:
        raise ValueError('Serato 视频盘路由无效')
    return payload


def accept_serato_frame(previous_epoch, previous_sequence, payload):
    frame = validate_serato_frame(payload)
    if frame['epoch'] != previous_epoch:
        return True
    _integer(previous_sequence, 'previous_sequence')
    return frame['sequence'] > previous_sequence


def validate_program_payload(payload):
    """Validate the renderer's atomic file snapshot, including lyric timelines."""
    program = _object(payload, '客屏快照')
    state = _object(program.get('state'), 'state')
    if state.get('selectedSource') not in SOURCES:
        raise ValueError('歌词来源无效')
    if state.get('scene') not in SCENES:
        raise ValueError('视觉场景无效')
    if state.get('mode', 'replay') not in ('replay', 'serato', 'rekordbox', 'rekordbox_ax'):
        raise ValueError('客屏输入模式无效')
    if 'activeDeck' in state and state['activeDeck'] not in ('A', 'B'):
        raise ValueError('客屏有效播放盘无效')
    if 'effectiveSource' in state and state['effectiveSource'] not in ('A', 'B', 'none'):
        raise ValueError('客屏当前歌词来源无效')
    for name in ('playing', 'loopEnabled', 'outputOpen', 'stale', 'bridgeRunning'):
        if name in state:
            _boolean(state[name], f'state.{name}')
    if 'speed' in state:
        _number(state['speed'], 'state.speed', -20 if state.get('mode') in ('serato', 'rekordbox', 'rekordbox_ax') else .8,
                20 if state.get('mode') in ('serato', 'rekordbox', 'rekordbox_ax') else 1.2)
    for name in ('deckA', 'deckB', 'lyric', 'diagnostics'):
        if name in state:
            _object(state[name], f'state.{name}')
    if 'library' in state and not isinstance(state['library'], list):
        raise ValueError('曲库状态无效')
    for name in ('modeLabel', 'status', 'notice', 'nativeCore'):
        if name in state:
            _string(state[name], f'state.{name}', 8192, allow_empty=True)
    _decks(program.get('raw'), allow_empty=True, live=state.get('mode') in ('serato', 'rekordbox', 'rekordbox_ax'),
           allow_partial=state.get('mode') in ('rekordbox', 'rekordbox_ax'))
    if 'style' in state:
        from app.styles import validate_style
        validate_style(state['style'])
    from app.live_settings import validate_offsets, validate_output_target
    if 'lyricOffsets' in state:
        validate_offsets(state['lyricOffsets'])
    if 'outputTarget' in state:
        validate_output_target(state['outputTarget'])
    if 'screenLayers' in state:
        from app.screen_layers import validate_layers
        validate_layers(state['screenLayers'])
    for field in ('screenTextValidUntilNs', 'guestMessageValidUntilNs'):
        if field in state:
            _integer(state[field], '文字有效期')
    if 'resumeSource' in state and state['resumeSource'] not in ('auto', 'A', 'B'):
        raise ValueError('歌词恢复来源无效')
    if state.get('outputDestination', 'standalone') not in ('standalone', 'serato_overlay'):
        raise ValueError('字幕输出目标无效')
    if state.get('mode') in ('serato', 'rekordbox', 'rekordbox_ax'):
        _number(state.get('videoFader', -1), 'videoFader', -1, 1)
        _integer(state.get('faderValidUntilNs', 0), 'faderValidUntilNs')
        if {state.get('faderLeftSource', 'A'), state.get('faderRightSource', 'B')} != {'A', 'B'}:
            raise ValueError('客屏视频盘路由无效')
    timelines = _object(program.get('timelines'), 'timelines')
    for version_id, timeline in timelines.items():
        _string(version_id, 'timeline.versionId', 256)
        try:
            validate_timeline(timeline, version_id)
        except (TypeError, KeyError, OverflowError) as exc:
            raise ValueError('歌词时间轴格式无效') from exc
    return payload
