"""SQLite single-writer index; no recognition requests or audio playback."""
import copy
import hashlib
import json
import math
import sqlite3
import wave
from pathlib import Path

from app.lyric_cache import (atomic_bytes, cache_filename, checksum, encode_json, lyric_source_label,
                             plain_directory, portable_timeline, read_bytes, read_json)

AUDIO_EXTENSIONS = {'.wav', '.mp3', '.flac', '.m4a', '.aiff', '.aif', '.ogg'}


def timeline_needs_review(timeline):
    correction = timeline.get('correction') if isinstance(timeline, dict) else None
    if lyric_source_label(timeline) == '本地识别' and (
            not isinstance(correction, dict) or correction.get('status') != 'applied'):
        return True
    return bool(isinstance(correction, dict) and
                (correction.get('status') == 'needs_review' or correction.get('review')))


def inspect_audio(path):
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError('请选择支持的普通音频文件')
    before = source.stat()
    digest = hashlib.sha256()
    with source.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    duration = None
    if source.suffix.lower() == '.wav':
        with wave.open(str(source), 'rb') as handle:
            if handle.getframerate() <= 0:
                raise ValueError('WAV 采样率无效')
            duration = handle.getnframes() * 1000.0 / handle.getframerate()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_ctime_ns, before.st_dev) != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_ctime_ns, after.st_dev):
        raise ValueError('文件在核验期间发生变化，请等待写入完成')
    return {'versionId': 'sha256:' + digest.hexdigest(), 'path': str(source),
            'title': source.stem, 'size': after.st_size, 'mtimeNs': after.st_mtime_ns, 'inode': after.st_ino,
            'ctimeNs': after.st_ctime_ns, 'device': after.st_dev,
            'durationMs': duration, 'quality': 'unavailable', 'status': '已核验文件，等待歌词', 'isDemo': False}


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('时码必须是有限且非负的毫秒数')
    return float(value)


def validate_timeline(payload, expected_version, expected_duration=None):
    if not isinstance(payload, dict) or type(payload.get('schemaVersion')) is not int or payload.get('schemaVersion') != 1:
        raise ValueError('不支持的歌词协议版本')
    if payload.get('versionId') != expected_version:
        raise ValueError('歌词与实际音频版本不匹配，已拒绝绑定')
    duration = _time(payload.get('durationMs'))
    if duration <= 0 or (expected_duration is not None and abs(duration - expected_duration) > 20):
        raise ValueError('歌词源时长与音频不匹配')
    if payload.get('quality') not in ('sentence', 'word'):
        raise ValueError('歌词等级只能为 sentence 或 word')
    segments = payload.get('segments')
    if not isinstance(segments, list) or not segments or len(segments) > 10000:
        raise ValueError('歌词片段数量无效')
    previous_end = 0.0
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError('歌词片段格式无效')
        start, end = _time(segment.get('startMs')), _time(segment.get('endMs'))
        text = segment.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError('歌词文本无效')
        if start < previous_end or end <= start or end > duration:
            raise ValueError('歌词片段重叠、越界或顺序错误')
        previous_end = end
        if payload['quality'] == 'word':
            words = segment.get('words')
            if not isinstance(words, list) or not words or len(words) > 500:
                raise ValueError('字级歌词缺少有效时码')
            word_end = start
            joined = ''
            for word in words:
                if not isinstance(word, dict):
                    raise ValueError('字级时码格式错误')
                ws, we = _time(word.get('startMs')), _time(word.get('endMs'))
                wt = word.get('text')
                if not isinstance(wt, str) or not wt or ws < word_end or we <= ws or we > end:
                    raise ValueError('字级时码重叠、越界或内容错误')
                joined += wt
                word_end = we
            if joined != text:
                raise ValueError('字级文本与句文本不一致')
    return payload


class Library:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cache_directory = plain_directory(self.directory / 'lyrics-cache', create=True)
        self._timeline_cache = {}
        self.connection = sqlite3.connect(self.directory / 'index.sqlite')
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.executescript('''
        CREATE TABLE IF NOT EXISTS tracks(version_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bindings(path TEXT PRIMARY KEY, version_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS timelines(version_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cache_metadata(name TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        self.connection.commit()
        self._migrate_timeline_files()

    def _migrate_timeline_files(self):
        """One-time migration only; subsequently a missing/bad file stays visible."""
        if self.connection.execute('SELECT value FROM cache_metadata WHERE name=?', ('file_cache_v1',)).fetchone():
            return
        for version, serialized in self.connection.execute('SELECT version_id,payload FROM timelines').fetchall():
            try:
                path = self.cache_directory / cache_filename(version)
                if path.exists() or path.is_symlink():
                    continue
                payload = json.loads(serialized)
                track = self.get_track(version)
                validate_timeline(payload, version, track.get('durationMs') if track else None)
                atomic_bytes(path, encode_json(portable_timeline(payload)))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        self.connection.execute('INSERT OR REPLACE INTO cache_metadata VALUES (?,?)', ('file_cache_v1', 'complete'))
        self.connection.commit()

    def interrupted_jobs(self):
        rows = self.connection.execute("SELECT job_id,payload FROM jobs WHERE state IN ('queued','running')").fetchall()
        paths = []
        for job_id, payload in rows:
            data = json.loads(payload)
            self.connection.execute('UPDATE jobs SET state=? WHERE job_id=?', ('interrupted', job_id))
            if data.get('type') == 'file_identity' and data.get('path'):
                paths.append(data['path'])
        self.connection.commit()
        return list(dict.fromkeys(paths))

    def add_track(self, track):
        value = dict(track)
        current = self.get_track(value['versionId'])
        cached = self.timeline(value['versionId'], expected_duration=value.get('durationMs'))
        if cached:
            value['quality'] = cached['quality']
            value['status'] = current['status'] if current and current.get('quality') in ('word', 'sentence') else '已匹配本地歌词缓存；质量尚未声学校验'
            if value.get('durationMs') is None:
                value['durationMs'] = cached['durationMs']
        self.connection.execute('INSERT OR REPLACE INTO tracks VALUES (?,?)',
                                (value['versionId'], json.dumps(value, ensure_ascii=False)))
        self.connection.execute('INSERT OR REPLACE INTO bindings VALUES (?,?)', (value['path'], value['versionId']))
        self.connection.commit()

    def get_track(self, version_id):
        row = self.connection.execute('SELECT payload FROM tracks WHERE version_id=?', (version_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def tracks(self):
        return [json.loads(row[0]) for row in self.connection.execute('SELECT payload FROM tracks ORDER BY rowid DESC')]

    def _cache_record(self, version_id, expected_duration=None, *, copy_payload=True):
        """Fully inspect changed files once; healthy-list reads need no lyric copies."""
        try:
            path = self.cache_directory / cache_filename(version_id)
            if path.is_symlink():
                raise ValueError('缓存文件不能为符号链接')
            details = path.stat()
            signature = (details.st_size, details.st_mtime_ns, details.st_ino, details.st_ctime_ns, details.st_dev)
            memo = self._timeline_cache.get(version_id)
            if memo and memo[0] == signature:
                payload = memo[1]
            else:
                payload = read_json(path)
                validate_timeline(payload, version_id)
                payload = portable_timeline(payload)
                self._timeline_cache[version_id] = (signature, payload)
        except FileNotFoundError:
            self._timeline_cache.pop(version_id, None)
            return 'missing', None
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            self._timeline_cache.pop(version_id, None)
            return 'invalid', None
        # Structure, source version and all word intervals were validated for
        # this exact file signature. A new source-duration expectation is only
        # a scalar check and must not invalidate an otherwise sound file memo.
        try:
            if expected_duration is not None:
                expected = _time(expected_duration)
                if expected <= 0 or abs(payload['durationMs'] - expected) > 20:
                    return 'invalid', None
        except (ValueError, TypeError, OverflowError):
            return 'invalid', None
        return 'ready', copy.deepcopy(payload) if copy_payload else payload

    def timeline(self, version_id, expected_duration=None):
        if expected_duration is None:
            track = self.get_track(version_id)
            expected_duration = track.get('durationMs') if track else None
        return self._cache_record(version_id, expected_duration)[1]

    def add_timeline(self, payload, verified_audio=None):
        if not isinstance(payload, dict):
            raise ValueError('歌词片段格式无效')
        track = self.get_track(payload.get('versionId'))
        if not track:
            raise ValueError('先导入对应的音频文件，再导入歌词')
        proof = verified_audio if verified_audio is not None else inspect_audio(track['path'])
        current = Path(track['path']).stat()
        if (proof['versionId'] != track['versionId'] or proof['path'] != track['path'] or
                (proof.get('size'), proof.get('mtimeNs'), proof.get('inode'), proof.get('ctimeNs'), proof.get('device')) !=
                (current.st_size, current.st_mtime_ns, current.st_ino, current.st_ctime_ns, current.st_dev)):
            raise ValueError('原文件已被替换，请重新导入')
        validate_timeline(payload, track['versionId'], track['durationMs'])
        payload = portable_timeline(payload)
        path = self.cache_directory / cache_filename(track['versionId'])
        previous = read_bytes(path) if path.exists() else None
        atomic_bytes(path, encode_json(payload))
        try:
            track.update(quality=payload['quality'], status='已绑定外部歌词；质量尚未声学校验')
            self.connection.execute('INSERT OR REPLACE INTO timelines VALUES (?,?)', (track['versionId'], json.dumps(payload, ensure_ascii=False)))
            self.connection.execute('UPDATE tracks SET payload=? WHERE version_id=?', (json.dumps(track, ensure_ascii=False), track['versionId']))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            if previous is not None:
                atomic_bytes(path, previous)
            else:
                path.unlink(missing_ok=True)
            raise
        finally:
            self._timeline_cache.pop(track['versionId'], None)

    @staticmethod
    def _source_state(track):
        try:
            details = Path(track['path']).stat()
        except (OSError, KeyError, TypeError):
            return 'source_missing'
        signature = (details.st_size, details.st_mtime_ns, details.st_ino, details.st_ctime_ns, details.st_dev)
        proof = tuple(track.get(field) for field in ('size', 'mtimeNs', 'inode', 'ctimeNs', 'device'))
        return 'ready' if signature == proof else 'source_changed'

    def offline_tracks(self):
        labels = {'ready': '离线可播', 'missing': '尚未准备歌词', 'invalid': '歌词缓存损坏，需恢复或重新准备',
                  'source_changed': '音频文件已变化，需重新核验', 'source_missing': '找不到原音频，需重新定位'}
        result = []
        current_versions = {row[0] for row in self.connection.execute('SELECT DISTINCT version_id FROM bindings')}
        for track in self.tracks():
            if track['versionId'] not in current_versions:
                continue
            cache_state, payload = self._cache_record(track['versionId'], track.get('durationMs'), copy_payload=False)
            source_state = self._source_state(track)
            state = source_state if cache_state == 'ready' and source_state != 'ready' else cache_state
            review = timeline_needs_review(payload)
            lyric_source = lyric_source_label(payload)
            cache_path = self.cache_directory / cache_filename(track['versionId'])
            result.append(dict(track, offlineReady=state == 'ready', offlineState=state,
                               offlineLabel=' · '.join(part for part in (lyric_source, labels[state],
                                                                         '待复核' if review else '') if part),
                               cachePath=str(cache_path), needsReview=review))
        return result

    def cache_summary(self):
        tracks = self.offline_tracks()
        ready = sum(track['offlineReady'] for track in tracks)
        invalid = sum(track['offlineState'] == 'invalid' for track in tracks)
        return {'total': len(tracks), 'ready': ready, 'missing': len(tracks) - ready - invalid,
                'invalid': invalid, 'review': sum(track['needsReview'] for track in tracks),
                'cacheDirectory': str(self.cache_directory)}

    def export_cache(self, destination):
        target = plain_directory(destination, create=True)
        if target.resolve() == self.cache_directory.resolve():
            raise ValueError('备份请选择其他文件夹')
        report = {'total': 0, 'exported': 0, 'rejected': 0, 'errors': [], 'directory': str(target)}
        entries = []
        for path in sorted(self.cache_directory.glob('*.json')):
            report['total'] += 1
            version = 'sha256:' + path.stem
            track = self.get_track(version)
            state, payload = self._cache_record(version, track.get('durationMs') if track else None)
            if state != 'ready':
                report['rejected'] += 1
                report['errors'].append({'file': path.name, 'reason': '歌词缓存无效'})
                continue
            data = encode_json(portable_timeline(payload))
            atomic_bytes(target / cache_filename(version), data)
            entries.append({'versionId': version, 'file': cache_filename(version), 'sha256': checksum(data)})
            report['exported'] += 1
        atomic_bytes(target / 'manifest.json', encode_json({'schemaVersion': 1, 'kind': 'baofang-lyric-cache', 'entries': entries}))
        return report

    def import_cache(self, source):
        origin = plain_directory(source)
        if origin.resolve() == self.cache_directory.resolve():
            raise ValueError('请选择备份文件夹')
        manifest = read_json(origin / 'manifest.json')
        if (not isinstance(manifest, dict) or type(manifest.get('schemaVersion')) is not int
                or manifest.get('schemaVersion') != 1 or manifest.get('kind') != 'baofang-lyric-cache'
                or not isinstance(manifest.get('entries'), list) or len(manifest['entries']) > 10000):
            raise ValueError('不支持的歌词库清单')
        report = {'total': len(manifest['entries']), 'imported': 0, 'reused': 0, 'matched': 0,
                  'pending': 0, 'rejected': 0, 'errors': [], 'directory': str(self.cache_directory)}
        seen = set()
        for entry in manifest['entries']:
            try:
                if not isinstance(entry, dict):
                    raise ValueError('歌词库条目格式无效')
                version = entry.get('versionId')
                filename = cache_filename(version)
                if entry.get('file') != filename or version in seen:
                    raise ValueError('歌词库文件名或重复版本无效')
                seen.add(version)
                data = read_bytes(origin / filename)
                if entry.get('sha256') != checksum(data):
                    raise ValueError('歌词库文件校验失败')
                payload = json.loads(data.decode('utf-8'))
                track = self.get_track(version)
                validate_timeline(payload, version, track.get('durationMs') if track else None)
                payload = portable_timeline(payload)
                existing = self.timeline(version)
                if existing:
                    # Recovery never replaces a corrected/live cache implicitly.
                    report['reused'] += 1
                else:
                    atomic_bytes(self.cache_directory / filename, encode_json(payload))
                    self._timeline_cache.pop(version, None)
                    report['imported'] += 1
                if track:
                    self.add_track(track)
                    report['matched'] += 1
                else:
                    report['pending'] += 1
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
                report['rejected'] += 1
                report['errors'].append({'file': entry.get('file', '') if isinstance(entry, dict) else '', 'reason': str(exc)})
        return report

    def record_job(self, job_id, state, payload):
        self.connection.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)', (job_id, state, json.dumps(payload, ensure_ascii=False)))
        self.connection.commit()

    def close(self):
        self.connection.close()
