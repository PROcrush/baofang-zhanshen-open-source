"""Map verified reader frames to our source-time clock without guessing raw units."""
import hashlib
import json
import math
from pathlib import Path

from app.messages import validate_serato_frame


def _duration(value):
    return value if type(value) in (int, float) and 0 < value < math.inf else 0


def _matches_duration(timeline, measured):
    duration = _duration(timeline.get('durationMs'))
    return bool(duration and (not measured or abs(duration - measured) <= 20))


def serato_lyric_index(tracks, timelines):
    """Index active lyrics by previously verified PCM identity."""
    groups = {}
    for track in tracks:
        content_id, version = track.get('audioContentId'), track['versionId']
        clock_duration = _duration(track.get('audioClockDurationMs'))
        timeline = timelines.get(version)
        if not isinstance(content_id, str) or not content_id or not clock_duration or not timeline or timeline.get('versionId') != version:
            continue
        # Copies of the same active words and timing are one choice. Compute
        # equivalence while indexing so the live clock never hashes lyrics.
        render_data = (timeline['durationMs'], timeline['quality'], timeline['segments'])
        digest = hashlib.sha256(json.dumps(render_data, sort_keys=True).encode()).digest()
        groups.setdefault(content_id, {}).setdefault((clock_duration, digest), (version, clock_duration))
    return {content_id: tuple(versions.values()) for content_id, versions in groups.items()}


def assign_serato_lyric_versions(decks, index, timelines):
    """Resolve an active timeline from a freshly adapted, verified deck."""
    for deck in decks.values():
        deck.update(lyricVersionId='', lyricMatchReason='unverified')
        if not deck['identityReady']:
            continue
        version = deck['versionId']
        duration = _duration(deck.get('verifiedDurationMs', deck.get('durationMs')))
        exact = timelines.get(version)
        if exact is not None:
            if not _matches_duration(exact, duration):
                deck['lyricMatchReason'] = 'duration_mismatch'
            else:
                deck.update(lyricVersionId=version, lyricMatchReason='exact')
            continue
        clock_duration = _duration(deck.get('audioClockDurationMs'))
        candidates = [key for key, clock in index.get(deck.get('audioContentId'), ())
                      if clock_duration and clock == clock_duration]
        matches = [key for key in candidates if key in timelines and _matches_duration(timelines[key], duration)]
        if len(matches) == 1:
            deck.update(lyricVersionId=matches[0], lyricMatchReason='content')
        elif matches:
            deck.update(lyricVersionId=None, lyricMatchReason='ambiguous')
        else:
            deck['lyricMatchReason'] = 'duration_mismatch' if candidates else 'missing'


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
        measured_duration = _duration(proof.get('durationMs')) if ready else 0
        # The producer's raw length uses an unverified unit. Never clamp a real
        # observation against it or pretend a fabricated duration is the track length.
        duration = measured_duration if position <= measured_duration else 0
        raw[key] = {'versionId': version, 'title': Path(path).name if path else '未加载曲目', 'path': path,
                    'positionMs': position, 'durationMs': duration, 'speed': item['velocity'],
                    'playing': item['velocity'] != 0, 'capturedNs': frame['capturedNs'],
                    'validUntilNs': frame['validUntilNs'], 'generation': item['source_identity'],
                    'identityReady': ready, 'sourceIdentity': item['source_identity'],
                    'verifiedDurationMs': measured_duration,
                    'identityProof': {key: proof[key] for key in ('path', 'size', 'mtimeNs', 'inode', 'ctimeNs', 'device')} if ready else None}
        if ready:
            content_id = proof.get('audioContentId')
            clock_duration = _duration(proof.get('audioClockDurationMs'))
            if isinstance(content_id, str) and 0 < len(content_id) <= 128 and clock_duration:
                raw[key].update(audioContentId=content_id, audioClockDurationMs=clock_duration)
    return raw
