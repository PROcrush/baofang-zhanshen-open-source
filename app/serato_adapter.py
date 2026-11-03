"""Map verified reader frames to our source-time clock without guessing raw units."""
import hashlib
from pathlib import Path

from app.messages import validate_serato_frame


def file_signature(path):
    current = Path(path).stat()
    return current.st_size, current.st_mtime_ns, current.st_ino, current.st_ctime_ns, current.st_dev


def proof_is_current(proof, path):
    if not proof or not path:
        return False
    try:
        source = Path(path).resolve(strict=True)
        if proof.get('path') != str(source):
            return False
        return tuple(proof[key] for key in ('size', 'mtimeNs', 'inode', 'ctimeNs', 'device')) == file_signature(source)
    except (OSError, KeyError, ValueError, TypeError):
        return False


def adapt_serato_frame(frame, file_proofs):
    validate_serato_frame(frame)
    raw = {}
    for item in frame['decks']:
        key = 'AB'[item['deck_index']]
        path = item['audio_path']
        try:
            proof = (file_proofs.get(path) or file_proofs.get(str(Path(path).resolve(strict=True)))) if path else None
        except OSError:
            proof = None
        ready = proof_is_current(proof, path)
        version = proof['versionId'] if ready else 'pending:' + hashlib.sha256(path.encode()).hexdigest()
        position = item['position_seconds'] * 1000
        duration = float(proof.get('durationMs') or 0) if ready else 0
        # The producer's raw length uses an unverified unit. Never clamp a real
        # observation against it or pretend a fabricated duration is the track length.
        duration = duration if position <= duration else 0
        raw[key] = {'versionId': version, 'title': Path(path).name if path else '未加载曲目', 'path': path,
                    'positionMs': position, 'durationMs': duration, 'speed': item['velocity'],
                    'playing': item['velocity'] != 0, 'capturedNs': frame['capturedNs'],
                    'validUntilNs': frame['validUntilNs'], 'generation': item['source_identity'],
                    'identityReady': ready, 'sourceIdentity': item['source_identity'],
                    'identityProof': {key: proof[key] for key in ('path', 'size', 'mtimeNs', 'inode', 'ctimeNs', 'device')} if ready else None}
    return raw
