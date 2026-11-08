"""Map verified reader frames to our source-time clock without guessing raw units."""
import hashlib
from pathlib import Path
from unicodedata import normalize

from app.lyric_cache import lyric_source_label
from app.messages import validate_serato_frame


def song_filename(path):
    return normalize('NFC', Path(path).name)


def filename_lyric_versions(tracks, timelines):
    """Index exact filenames; existing local drafts are a fallback."""
    paths = {}
    versions = {}
    local_versions = {}
    for track in tracks:
        path = track['path']
        name = song_filename(path)
        paths.setdefault(name, set()).add(path)
        version = track['versionId']
        timeline = timelines.get(version)
        if timeline:
            candidates = local_versions if lyric_source_label(timeline) == '本地识别' else versions
            candidates.setdefault(name, version)
    return {name: versions.get(name, local_versions.get(name, '')) if len(sources) == 1 else None
            for name, sources in paths.items()}


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
