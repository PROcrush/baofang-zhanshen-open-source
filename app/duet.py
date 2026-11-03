"""Duet colours from singer tags already written in the lyrics.

KTV lyric files mark parts with a leading tag such as ``男：``, ``(女)``,
``【合】`` or ``M:``. A tag holds until the next tag. We only read tags; the
timeline itself is never changed. When the feature is on, a cleaned view of
the timeline drops the tag text (and the matching leading word tokens) so the
guest screen does not show it.
"""
import re

SINGERS = ('male', 'female', 'chorus')
_NAMES = {'男': 'male', 'm': 'male', '男声': 'male', '男生': 'male',
          '女': 'female', 'f': 'female', '女声': 'female', '女生': 'female',
          '合': 'chorus', '合唱': 'chorus', '男女': 'chorus', '齐': 'chorus', 'd': 'chorus', 'all': 'chorus'}
_TAG = re.compile(r'^\s*(?:[(\[（【<《]\s*(?P<a>男声|女声|男生|女生|男女|合唱|男|女|合|齐|M|F|D|ALL)\s*[)\]）】>》]'
                  r'|(?P<b>男声|女声|男生|女生|男女|合唱|男|女|合|齐|M|F|D|ALL)\s*[:：])\s*', re.IGNORECASE)

_cache = {}


def parse_singer(text):
    """Return (singer or None, text without the tag)."""
    match = _TAG.match(text or '')
    if not match:
        return None, text or ''
    name = (match.group('a') or match.group('b')).lower()
    rest = text[match.end():]
    if not rest.strip():
        return None, text
    return _NAMES[name], rest


def _strip_words(words, tag_length):
    """Drop leading timed tokens that only carry the tag characters."""
    if not words:
        return words
    remaining, index = tag_length, 0
    while index < len(words) and remaining > 0:
        size = len(words[index].get('text', ''))
        if size > remaining:
            return words  # tag shares a token with lyric text: keep tokens as they are
        remaining -= size
        index += 1
    return words[index:] if remaining == 0 else words


def duet_view(timeline):
    """Cleaned copy with a 'singer' on every segment; cached per timeline object."""
    if not timeline or not timeline.get('segments'):
        return timeline
    key = id(timeline)
    cached = _cache.get(key)
    if cached and cached[0] is timeline:
        return cached[1]
    singer, segments, tagged = None, [], False
    for row in timeline['segments']:
        found, rest = parse_singer(row.get('text', ''))
        value = dict(row)
        if found:
            tagged = True
            singer = found
            original = row.get('words') or []
            if not original:
                value['text'] = rest
            else:
                words = _strip_words(original, len(row['text']) - len(rest))
                if words is not original and words:
                    # 去掉只装标签的字时码；句子起点不变，不造新时间
                    value['text'], value['words'] = rest, words
        value['singer'] = singer
        segments.append(value)
    view = dict(timeline, segments=segments, duetTagged=tagged)
    if len(_cache) > 64:
        _cache.clear()
    _cache[key] = (timeline, view)
    return view


def singer_at(view, position_ms):
    """Singer of the line on screen at a source position, or the next line."""
    if not view or not view.get('segments'):
        return None
    for row in view['segments']:
        if position_ms < row['endMs']:
            return row.get('singer')
    return None
