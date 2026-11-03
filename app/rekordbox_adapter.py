"""Validate our live bridge and bind each observed deck to its exact local file."""
import hashlib
import math
from pathlib import Path

from app.serato_adapter import proof_is_current


def validate_rekordbox_frame(frame):
    if (not isinstance(frame, dict) or type(frame.get('protocolVersion')) is not int
            or frame['protocolVersion'] != 1 or frame.get('type') != 'rekordbox_frame'
            or frame.get('mode') != 'rekordbox' or frame.get('producerVerified') is not True
            or frame.get('positionOnly') is not True):
        raise ValueError('rekordbox 消息身份或协议无效')
    epoch = frame.get('epoch')
    if not isinstance(epoch, str) or not epoch.startswith('rekordbox:') or len(epoch) > 128:
        raise ValueError('rekordbox 会话无效')
    for key in ('sequence', 'capturedNs', 'validUntilNs'):
        value = frame.get(key)
        if type(value) is not int or not 0 < value < (1 << 63):
            raise ValueError('rekordbox 时序无效')
    if not 0 <= frame['validUntilNs'] - frame['capturedNs'] <= 350_000_000:
        raise ValueError('rekordbox 播放观测有效期无效')
    decks = frame.get('decks')
    if not isinstance(decks, list) or len(decks) != 2:
        raise ValueError('rekordbox 必须提供两盘状态')
    seen = set()
    for deck in decks:
        if not isinstance(deck, dict) or deck.get('deck') not in ('A', 'B') or deck['deck'] in seen:
            raise ValueError('rekordbox 盘位无效')
        seen.add(deck['deck'])
        if type(deck.get('available')) is not bool or type(deck.get('playRequested')) is not bool:
            raise ValueError('rekordbox 盘状态无效')
        path = deck.get('trackPath')
        if not isinstance(path, str) or len(path) > 4096 or any(c in path for c in ('\0', '\n', '\r')):
            raise ValueError('rekordbox 曲目路径无效')
        if not deck['available']:
            if path or deck.get('positionSeconds') is not None or deck.get('normalRate') is not None or deck['playRequested']:
                raise ValueError('rekordbox 不可用盘必须清空数据')
            continue
        if not Path(path).is_absolute():
            raise ValueError('rekordbox 需要本地绝对路径')
        for field, maximum in (('positionSeconds', 86400), ('normalRate', 5)):
            value = deck.get(field)
            if type(value) not in (int, float) or not 0 <= value <= maximum or not math.isfinite(value):
                raise ValueError('rekordbox 播放位置或速度设定无效')
    fader = frame.get('fader')
    if fader is not None:
        if (not isinstance(fader, dict) or fader.get('leftSource') not in ('A', 'B')
                or fader.get('rightSource') not in ('A', 'B') or fader['leftSource'] == fader['rightSource']
                or type(fader.get('normalized')) not in (int, float)
                or not 0 <= fader['normalized'] <= 1 or not math.isfinite(fader['normalized'])):
            raise ValueError('rekordbox 混音路由无效')
    return frame


def accept_rekordbox_frame(previous_epoch, previous_sequence, frame):
    validate_rekordbox_frame(frame)
    return frame['epoch'] != previous_epoch or frame['sequence'] > previous_sequence


def adapt_rekordbox_frame(frame, file_proofs):
    validate_rekordbox_frame(frame)
    raw = {}
    for item in frame['decks']:
        if not item['available']:
            continue
        path = item['trackPath']
        proof = file_proofs.get(path)
        ready = proof_is_current(proof, path)
        version = proof['versionId'] if ready else 'pending:' + hashlib.sha256(path.encode()).hexdigest()
        position = item['positionSeconds'] * 1000
        duration = float(proof.get('durationMs') or 0) if ready else 0
        raw[item['deck']] = {'versionId': version, 'title': Path(path).stem, 'path': path,
                            'positionMs': position, 'durationMs': duration if position <= duration else 0,
                            'speed': item['normalRate'], 'playing': item['playRequested'],
                            'clockMode': 'observed', 'capturedNs': frame['capturedNs'],
                            'validUntilNs': frame['validUntilNs'], 'generation': 0,
                            'identityReady': ready,
                            'identityProof': {key: proof[key] for key in
                                              ('path', 'size', 'mtimeNs', 'inode', 'ctimeNs', 'device')} if ready else None}
    return raw
