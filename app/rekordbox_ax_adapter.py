"""Bind AX observations to an explicitly confirmed local file, never a title guess.

AX exposes a title and coarse source time, not a file identity.  The caller must
ask the user to choose and confirm each deck's file.  A binding lasts only for
that reader epoch and observation identity, and the file's stat proof remains
mandatory on every adaptation.  Equal titles and lengths alone are not proof
that two files are the same recording.
"""
import hashlib
import math
from pathlib import Path
import re

from app.serato_adapter import proof_is_current


MAX_SECONDS = 86_400
MAX_TTL_NS = 350_000_000
DURATION_TOLERANCE_MS = 250
PROOF_KEYS = ('path', 'size', 'mtimeNs', 'inode', 'ctimeNs', 'device')
VERSION_PATTERN = re.compile(r'sha256:[0-9a-f]{64}\Z')


def _number(value, minimum, maximum):
    # Bound before conversion/isfinite: huge JSON integers must fail cleanly.
    return (type(value) in (int, float) and minimum <= value <= maximum
            and math.isfinite(value))


def _text(value, maximum, *, empty=False):
    return (isinstance(value, str) and len(value) <= maximum
            and (empty or bool(value.strip()))
            and not any(c in value for c in ('\0', '\n', '\r'))
            and not any(0xD800 <= ord(c) <= 0xDFFF for c in value))


def validate_ax_frame(frame):
    """Validate the independent AX protocol; never accept memory-reader data."""
    if (not isinstance(frame, dict) or type(frame.get('protocolVersion')) is not int
            or frame['protocolVersion'] != 1 or frame.get('type') != 'rekordbox_ax_frame'
            or frame.get('mode') != 'rekordbox_ax' or frame.get('sourceKind') != 'accessibility'
            or frame.get('producerVerified') is not True or frame.get('positionOnly') is not True):
        raise ValueError('rekordbox AX 消息身份或协议无效')
    epoch = frame.get('epoch')
    if not _text(epoch, 128) or not epoch.startswith('rekordbox-ax:') or len(epoch) <= len('rekordbox-ax:'):
        raise ValueError('rekordbox AX 会话无效')
    for key in ('sequence', 'capturedNs', 'validUntilNs'):
        value = frame.get(key)
        if type(value) is not int or not 0 < value < (1 << 63):
            raise ValueError('rekordbox AX 时序无效')
    if not 0 < frame['validUntilNs'] - frame['capturedNs'] <= MAX_TTL_NS:
        raise ValueError('rekordbox AX 播放观测有效期无效')
    if 'fader' not in frame or frame['fader'] is not None:
        raise ValueError('rekordbox AX 当前只接受手动 A/B 选择')
    decks = frame.get('decks')
    if not isinstance(decks, list) or len(decks) != 2:
        raise ValueError('rekordbox AX 必须提供两盘状态')
    seen = set()
    for item in decks:
        if not isinstance(item, dict) or item.get('deck') not in ('A', 'B') or item['deck'] in seen:
            raise ValueError('rekordbox AX 盘位无效')
        seen.add(item['deck'])
        if type(item.get('available')) is not bool or item.get('playRequested') is not False:
            raise ValueError('rekordbox AX 盘状态无效')
        if not item['available']:
            if (item.get('title') != '' or item.get('observationId') != ''
                    or any(item.get(key) is not None for key in
                           ('positionSeconds', 'remainingSeconds', 'normalRate'))):
                raise ValueError('rekordbox AX 不可用盘必须清空数据')
            continue
        if not _text(item.get('title'), 512) or not _text(item.get('observationId'), 128):
            raise ValueError('rekordbox AX 曲目观测无效')
        if (not _number(item.get('positionSeconds'), 0, MAX_SECONDS)
                or not _number(item.get('remainingSeconds'), 0, MAX_SECONDS)
                or not _number(item.get('normalRate'), 0, 5)):
            raise ValueError('rekordbox AX 播放位置或速度无效')
        if not 0 < item['positionSeconds'] + item['remainingSeconds'] <= MAX_SECONDS:
            raise ValueError('rekordbox AX 观测总时长无效')
    return frame


def accept_ax_frame(previous_epoch, previous_sequence, frame):
    validate_ax_frame(frame)
    return frame['epoch'] != previous_epoch or frame['sequence'] > previous_sequence


def _proof_valid(proof, path):
    return (isinstance(proof, dict) and _text(path, 8192) and Path(path).is_absolute()
            and isinstance(proof.get('versionId'), str)
            and VERSION_PATTERN.fullmatch(proof['versionId']) is not None
            and _number(proof.get('durationMs'), .001, MAX_SECONDS * 1000)
            and proof_is_current(proof, path))


def _observed_duration(item):
    return (item['positionSeconds'] + item['remainingSeconds']) * 1000


def make_ax_binding(frame, deck, proof):
    """Return a binding only after the caller's explicit per-deck confirmation.

    This helper checks the captured observation and selected file, but cannot
    replace the user's confirmation: AX has no exact pathname attribute.
    """
    validate_ax_frame(frame)
    if deck not in ('A', 'B'):
        raise ValueError('请选择 A 盘或 B 盘')
    item = next(item for item in frame['decks'] if item['deck'] == deck)
    if not item['available']:
        raise ValueError('此盘尚无可绑定的播放观测')
    path = proof.get('path') if isinstance(proof, dict) else None
    if not _proof_valid(proof, path):
        raise ValueError('请先核验所选音频文件和实际时长')
    if abs(_observed_duration(item) - proof['durationMs']) > DURATION_TOLERANCE_MS:
        raise ValueError('所选文件时长与此盘观测不符，已拒绝绑定')
    return {'epoch': frame['epoch'], 'observationId': item['observationId'],
            'title': item['title'], 'path': path, 'versionId': proof['versionId'],
            'durationMs': proof['durationMs']}


def _binding_ready(frame, item, binding, proof):
    if not isinstance(binding, dict):
        return False
    if (binding.get('epoch') != frame['epoch'] or binding.get('observationId') != item['observationId']
            or binding.get('title') != item['title'] or not _proof_valid(proof, binding.get('path'))
            or binding.get('versionId') != proof['versionId']
            or not _number(binding.get('durationMs'), .001, MAX_SECONDS * 1000)):
        return False
    total = _observed_duration(item)
    return (abs(total - binding['durationMs']) <= DURATION_TOLERANCE_MS
            and abs(total - proof['durationMs']) <= DURATION_TOLERANCE_MS
            and abs(binding['durationMs'] - proof['durationMs']) <= 20)


def adapt_ax_frame(frame, bindings, file_proofs):
    """Produce the existing observed-clock raw schema, hiding unbound versions."""
    validate_ax_frame(frame)
    if not isinstance(bindings, dict) or not isinstance(file_proofs, dict):
        raise ValueError('rekordbox AX 文件绑定索引无效')
    raw = {}
    for item in frame['decks']:
        if not item['available']:
            continue
        side = item['deck']
        binding = bindings.get(side)
        path = binding.get('path') if isinstance(binding, dict) else None
        proof = file_proofs.get(path) if isinstance(path, str) else None
        ready = _binding_ready(frame, item, binding, proof)
        token = '\0'.join((frame['epoch'], side, item['observationId'], item['title']))
        version = proof['versionId'] if ready else 'pending:ax:' + hashlib.sha256(token.encode()).hexdigest()
        position = item['positionSeconds'] * 1000
        duration = proof['durationMs'] if ready else _observed_duration(item)
        # At the last coarse AX tick, the displayed position can exceed the
        # exact file length by <0.1s.  Keep the observation, without fabricating
        # a different file duration or rejecting an otherwise exact cache.
        duration = duration if position <= duration else 0
        status = 'ready' if ready else 'stale' if binding is not None else 'unbound'
        raw[side] = {'versionId': version, 'title': item['title'],
                     'path': path if ready else '', 'positionMs': position, 'durationMs': duration,
                     'speed': item['normalRate'], 'playing': False, 'clockMode': 'observed',
                     'capturedNs': frame['capturedNs'], 'validUntilNs': frame['validUntilNs'],
                     'generation': 0, 'identityReady': ready,
                     'identityProof': {key: proof[key] for key in PROOF_KEYS} if ready else None,
                     'axObservationId': item['observationId'], 'bindingStatus': status,
                     'bindingMessage': {'ready': '本次会话已确认文件',
                                        'unbound': '请为此盘选择并确认实际音频文件',
                                        'stale': '曲目、会话或文件已变化，请重新确认此盘文件'}[status]}
    return raw
