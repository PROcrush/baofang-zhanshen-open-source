"""Bind explicitly selected local subtitles to a verified audio file."""
import json
import math
from pathlib import Path

from app.lyric_cache import cache_filename, read_bytes, portable_timeline
from app.serato_adapter import proof_is_current
from app.storage import validate_timeline
from app.studio_tools import load_subtitles


def load_local_timeline(path, track):
    version = track['versionId']
    cache_filename(version)
    if not proof_is_current(track, track['path']):
        raise ValueError('歌曲文件已变化，请重新核验后导入字幕')
    source = Path(path).expanduser().absolute()
    before = source.stat()
    content = read_bytes(source).decode('utf-8-sig')
    duration = track.get('durationMs')
    if duration is None:
        from worker.studio_render import probe_media
        duration = float(probe_media(track['path'])['format']['duration']) * 1000
    if isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('本地字幕导入需要有效的音频文件时长')
    payload = json.loads(content) if source.suffix.lower() == '.json' else None
    if isinstance(payload, dict) and payload.get('schemaVersion') == 1:
        # A versioned cache keeps its original identity; explicit import must
        # not silently turn another song's JSON into the currently loaded song.
        validate_timeline(payload, version, track.get('durationMs'))
        if payload['segments'][-1]['endMs'] > duration:
            raise ValueError('字幕时码超出当前歌曲长度')
    else:
        imported = load_subtitles(source, duration=duration / 1000)
        rows = imported['segments']
        word_timing = all(row.get('characters') for row in rows)
        segments = []
        for row in rows:
            segment = {'startMs': round(row['start'] * 1000, 6),
                       'endMs': round(row['end'] * 1000, 6), 'text': row['text']}
            if word_timing:
                segment['words'] = [{'startMs': round(token['start'] * 1000, 6),
                                     'endMs': round(token['end'] * 1000, 6), 'text': token['text']}
                                    for token in row['characters']]
            segments.append(segment)
        payload = {'schemaVersion': 1, 'versionId': version, 'durationMs': duration,
                   'quality': 'word' if word_timing else 'sentence',
                   'timingProvenance': 'imported_token' if word_timing else 'imported_sentence',
                   'acousticAccuracyVerified': False, 'segments': segments}
        validate_timeline(payload, version, duration)
    after = source.stat()
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise ValueError('字幕文件在导入期间改变，请重新选择')
    if not proof_is_current(track, track['path']):
        raise ValueError('歌曲文件在导入期间改变，请重新核验')
    return portable_timeline(payload)
