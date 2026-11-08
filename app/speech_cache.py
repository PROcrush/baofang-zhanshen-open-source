"""Read cached cloud speech proposals without treating them as active lyrics."""
from functools import lru_cache
from sqlite3 import Error as StorageError

from app.lyric_cache import atomic_bytes, cache_filename, checksum, encode_json, plain_directory, portable_timeline, read_json
from app.storage import validate_timeline


def speech_cache_path(runtime, version):
    return runtime / 'speech-cache' / cache_filename(version)


def read_speech_cache(runtime, version, duration=None):
    try:
        path = speech_cache_path(runtime, version)
        stat = path.lstat()
        signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        proposal = _read_speech_cache(path, version, signature)
        if proposal and proposal.get('timeline') and duration is not None and abs(proposal['timeline']['durationMs'] - duration) > 20:
            return {'text': proposal['text']} if proposal.get('text') else None
        return proposal
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return None


@lru_cache(maxsize=4)
def _read_speech_cache(path, version, signature):
    proposal = read_json(plain_directory(path.parent) / path.name)
    timeline = proposal.get('timeline')
    if timeline:
        validate_timeline(timeline, version)
        proposal['revisionId'] = checksum(encode_json(portable_timeline(timeline)))
    if not isinstance(proposal.get('text'), str) or not proposal['text'].strip():
        proposal.pop('text', None)
    if not timeline and 'text' not in proposal:
        return None
    return proposal


def write_speech_cache(runtime, version, proposal, config):
    existing = read_speech_cache(runtime, version) or {}
    result = {key: existing[key] for key in ('timeline', 'config', 'text') if key in existing}
    timeline = proposal.get('timeline')
    if timeline:
        result.update(timeline=portable_timeline(timeline), config=config)
    else:
        result['text'] = proposal['text']
    path = speech_cache_path(runtime, version)
    plain_directory(path.parent, create=True)
    atomic_bytes(path, encode_json(result))


def pending_speech(library, version, duration=None):
    read_pending = getattr(library, 'pending_lyric_revision', None)
    if read_pending:
        try:
            timeline = read_pending(version)
            if timeline:
                validate_timeline(timeline, version, duration)
                return timeline
        except (ValueError, TypeError, OverflowError, StorageError):
            pass
    return None


def standalone_candidate(library, version, cache):
    timeline = cache.get('timeline')
    reviewed = getattr(library, 'is_reviewed_lyric_revision', None)
    if timeline and reviewed and reviewed(version, cache['revisionId']):
        return None
    return timeline
