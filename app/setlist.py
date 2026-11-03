"""Tonight's set list: an ordered list of library versions, kept on this Mac.

Only version ids are stored. Titles and lyric readiness are read from the
library each time, so a list never claims a song is ready when it is not.
"""
import json
import os
from pathlib import Path

MAX_ITEMS = 300


def _valid_version(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 256


class Setlist:
    def __init__(self, path):
        self.path = Path(path)
        self.items = []
        self.played = set()
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('schemaVersion') == 1:
                items = [v for v in data.get('items', []) if _valid_version(v)]
                self.items = list(dict.fromkeys(items))[:MAX_ITEMS]
                self.played = {v for v in data.get('played', []) if v in self.items}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'schemaVersion': 1, 'items': self.items,
                                         'played': [v for v in self.items if v in self.played]},
                                        ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.path)

    def _commit(self, items, played):
        previous = self.items, self.played
        self.items, self.played = items, played
        try:
            self.save()
        except OSError:
            self.items, self.played = previous
            raise

    def add(self, version):
        if not _valid_version(version):
            raise ValueError('请选择曲库里的歌曲')
        if version in self.items:
            return False
        if len(self.items) >= MAX_ITEMS:
            raise ValueError(f'歌单最多 {MAX_ITEMS} 首')
        self._commit(self.items + [version], set(self.played))
        return True

    def remove(self, index):
        if not 0 <= index < len(self.items):
            raise ValueError('歌单里没有这一首')
        items = self.items[:index] + self.items[index + 1:]
        self._commit(items, self.played & set(items))

    def move(self, index, delta):
        target = index + delta
        if not 0 <= index < len(self.items) or not 0 <= target < len(self.items):
            return False
        items = list(self.items)
        items[index], items[target] = items[target], items[index]
        self._commit(items, set(self.played))
        return True

    def set_played(self, index, played):
        if not 0 <= index < len(self.items):
            raise ValueError('歌单里没有这一首')
        marks = set(self.played)
        (marks.add if played else marks.discard)(self.items[index])
        self._commit(list(self.items), marks)

    def mark_played(self, versions):
        """Mark songs seen on a deck. Returns True when something changed."""
        new = {v for v in versions if v in self.items} - self.played
        if not new:
            return False
        self._commit(list(self.items), self.played | new)
        return True

    def clear(self, played_only=False):
        if played_only:
            items = [v for v in self.items if v not in self.played]
            self._commit(items, set())
        else:
            self._commit([], set())

    def view(self, tracks, loaded=()):
        """Rows for the UI. `tracks` are library rows keyed by versionId."""
        by_version = {row.get('versionId'): row for row in tracks}
        loaded = set(loaded)
        rows, counts = [], {'ready': 0, 'review': 0, 'missing': 0, 'gone': 0}
        upcoming = None
        for index, version in enumerate(self.items):
            track = by_version.get(version)
            if not track:
                state, label = 'gone', '曲库里找不到'
            elif track.get('offlineReady') and track.get('needsReview'):
                state, label = 'review', '歌词待复核'
            elif track.get('offlineReady'):
                state, label = 'ready', '歌词就绪'
            else:
                state, label = 'missing', '还没歌词'
            counts[state] += 1
            played = version in self.played
            row = {'index': index, 'versionId': version, 'title': (track or {}).get('title') or '未知歌曲',
                   'durationText': (track or {}).get('durationText', ''), 'state': state, 'label': label,
                   'played': played, 'onDeck': version in loaded}
            if upcoming is None and not played and version not in loaded:
                upcoming = row
            rows.append(row)
        return {'items': rows, 'total': len(rows), 'played': sum(r['played'] for r in rows),
                'counts': counts, 'next': upcoming}
