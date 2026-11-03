"""Anonymous reference lookup. Only titles go online; original clocks stay local."""
import hashlib
import http.client
import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlencode

from worker.cloud_pipeline import _atomic

MAX_BYTES = 512_000
PROVIDERS = {'netease': '网易云音乐', 'lrclib': 'LRCLIB'}


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).lower() if c.isalnum())


def guess_title(title):
    value = re.sub(r'\.(wav|mp3|flac|m4a|aiff?|ogg|mp4)$', '', str(title), flags=re.I)
    value = re.sub(r'[（(\[].*?(?:remix|mix|edit|bootleg|重混|混音).*?[）)\]]', '', value, flags=re.I)
    return value.strip(' #_-')[:120]


def plain_lines(text):
    if not isinstance(text, str) or len(text) > 30_000:
        raise ValueError('参考歌词过长或格式无效')
    lines = []
    for line in text.splitlines():
        line = re.sub(r'\[[^\]]*\]', '', line).strip()
        if not normalized(line) or re.match(r'^(作词|作曲|编曲|制作人|词|曲|Lyricist|Composer)\s*[:：]', line, re.I):
            continue
        if len(line) > 300:
            continue
        lines.append(line)
    if not lines or len(lines) > 500:
        raise ValueError('未找到可用于匹配的参考歌词')
    return lines


def reference(provider, identity, title, artist, lyrics):
    if provider not in PROVIDERS or not str(identity).isdigit():
        raise ValueError('歌词来源无效')
    lines = plain_lines(lyrics)
    digest = hashlib.sha256('\n'.join(lines).encode()).hexdigest()
    url = ('https://music.163.com/song?id=' if provider == 'netease' else 'https://lrclib.net/api/get/') + str(identity)
    return {'id': provider + ':' + str(identity), 'provider': provider, 'providerLabel': PROVIDERS[provider],
            'title': str(title)[:120], 'artist': str(artist)[:120], 'url': url,
            'lines': lines, 'lyricsSha256': digest}


def validate_reference(value):
    if (not isinstance(value,dict) or not isinstance(value.get('id'),str)
            or not isinstance(value.get('lines'),list) or not all(isinstance(line,str) for line in value['lines'])):
        raise ValueError('参考歌词缓存格式无效，请重新搜索')
    provider, identity = value['id'].split(':', 1)
    clean = reference(provider, identity, value['title'], value['artist'], '\n'.join(value['lines']))
    if clean['lyricsSha256'] != value['lyricsSha256'] or clean['url'] != value['url']:
        raise ValueError('参考歌词缓存已改变，请重新搜索')
    return clean


class ReferenceTransport:
    def json(self, host, path, body=None):
        connection = http.client.HTTPSConnection(host, timeout=8)
        try:
            headers = {'User-Agent': 'BaofangZhanshen/0.12 (reference lyrics)', 'Accept': 'application/json'}
            if host == 'music.163.com':
                headers['Referer'] = 'https://music.163.com/'
            if body is not None:
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
            connection.request('POST' if body is not None else 'GET', path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(MAX_BYTES + 1)
            if response.status != 200 or len(data) > MAX_BYTES:
                raise ValueError('歌词源暂时未返回可用结果')
            return json.loads(data)
        finally:
            connection.close()


def _netease(query, transport):
    data = transport.json('music.163.com', '/api/search/get/web', urlencode({'s': query, 'type': 1, 'limit': 5, 'offset': 0}).encode())
    if data.get('code') != 200:
        raise ValueError('网易云暂时无法搜索')
    found = []
    for song in data.get('result', {}).get('songs', [])[:5]:
        identity = str(song.get('id', ''))
        if not identity.isdigit():
            continue
        try:
            data = transport.json('music.163.com', '/api/song/lyric?' + urlencode({'id': identity, 'lv': -1, 'kv': -1, 'tv': -1}))
            if data.get('code') != 200:
                continue
            found.append(reference('netease', identity, song.get('name', ''),
                                   ' / '.join(a.get('name', '') for a in song.get('artists', [])), data.get('lrc', {}).get('lyric', '')))
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            continue
    return found


def _lrclib(query, transport):
    data = transport.json('lrclib.net', '/api/search?' + urlencode({'q': query}))
    if not isinstance(data, list):
        raise ValueError('LRCLIB暂时无法搜索')
    found = []
    for song in data[:5]:
        try:
            found.append(reference('lrclib', song['id'], song['trackName'], song.get('artistName', ''),
                                   song.get('plainLyrics') or song.get('syncedLyrics') or ''))
        except (ValueError, KeyError, TypeError):
            continue
    return found


def search(title, artist, cache_directory, *, offline=False, transport=None):
    title, artist = str(title).strip(), str(artist).strip()
    if not normalized(title) or len(title) > 120 or len(artist) > 120:
        raise ValueError('请输入120字以内的原版歌名和歌手')
    query = title + (' ' + artist if artist else '')
    path = Path(cache_directory) / (hashlib.sha256(query.encode()).hexdigest() + '.json')
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('缓存过大')
        data = json.loads(path.read_text(encoding='utf-8'))
        candidates = [validate_reference(row) for row in data['candidates'][:10]]
        if candidates and data['query'] == query:
            return {'candidates': candidates, 'cached': True, 'query': query, 'notices': []}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if offline:
        raise ValueError('这首歌没有可用的原版歌词缓存；联网搜索一次后可离线匹配')
    transport = transport or ReferenceTransport()
    notices = []
    candidates = []
    for provider, lookup in (('netease', _netease), ('lrclib', _lrclib)):
        try:
            candidates = lookup(query, transport)
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            notices.append(PROVIDERS[provider] + '暂时不可用')
        if candidates:
            break
    if not candidates:
        raise ValueError('未找到可用的原版歌词，请调整歌名或歌手后重试；识别原文保留')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    _atomic(path, {'schemaVersion': 1, 'query': query, 'fetchedAt': int(time.time()), 'candidates': candidates})
    return {'candidates': candidates, 'cached': False, 'query': query, 'notices': notices}
