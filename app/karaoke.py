"""Pure source-time queries for supplied karaoke token intervals.

Offsets refer to the original Python string. A token can contain several
characters: its supplied interval stays intact and is never split into clocks.
"""
import math


def _finite(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _tokens(row):
    if not isinstance(row, dict):
        return None
    text, tokens = row.get('text'), row.get('characters')
    start, end = row.get('startMs'), row.get('endMs')
    if (not isinstance(text, str) or not text or not isinstance(tokens, list) or not tokens
            or not _finite(start) or not _finite(end) or start < 0 or end <= start):
        return None
    result, offset, previous_end = [], 0, start
    for token in tokens:
        if not isinstance(token, dict):
            return None
        word, first, last = token.get('text'), token.get('startMs'), token.get('endMs')
        if (not isinstance(word, str) or not word or not _finite(first) or not _finite(last)
                or first < previous_end or last <= first or last > end):
            return None
        following = offset + len(word)
        if text[offset:following] != word:
            return None
        result.append((offset, following, first, last))
        offset, previous_end = following, last
    return result if offset == len(text) else None


def token_cursor(row, position_ms):
    """Return (token start, token end, fraction), or None for sentence-only text.

    Invalid, incomplete or mismatched token data safely loses sweep capability.
    Querying any source position is independent of previous calls, so pauses,
    token gaps and backward seeks cannot continue an old animation.
    """
    tokens = _tokens(row)
    if tokens is None or not _finite(position_ms):
        return None
    for first, last, start, end in tokens:
        if position_ms < end:
            return first, last, max(0., (position_ms - start) / (end - start))
    end = len(row['text'])
    return end, end, 0.


def sweep_cursor(row, position_ms):
    """Prefer supplied tokens; otherwise estimate a wipe over the whole sentence.

    The fallback is one visual interval over the rendered line, not generated
    character timings. It is a pure source-time query, including pause/seek.
    """
    cursor = token_cursor(row, position_ms)
    if cursor is not None:
        return cursor
    if not isinstance(row, dict) or not _finite(position_ms):
        return None
    start, end, text = row.get('startMs'), row.get('endMs'), row.get('text')
    if (not isinstance(text, str) or not text or not _finite(start) or not _finite(end)
            or start < 0 or end <= start):
        return None
    return 0, len(text), max(0., min(1., (position_ms - start) / (end - start)))


def slice_cursor(cursor, offset, length):
    """Map a source cursor to a display-only substring without changing time."""
    first, last, fraction = cursor
    end = offset + length
    if last <= offset:
        return 0, 0, 0.
    if first >= end:
        return length, length, 0.
    # Keep a token's fraction across wrapped fragments by clipping its source
    # character interval; no per-character timestamps are stored or invented.
    edge = first + (last - first) * fraction
    local_first, local_last = max(0, first-offset), min(length, last-offset)
    return local_first, local_last, max(0., min(1., (edge-offset-local_first) / max(1, local_last-local_first)))


def sweep_color(base, highlight):
    """Keep distinct user colors, repairing effectively invisible wipes."""
    def rgb(value):
        return tuple(int(value[index:index+2], 16) for index in (1, 3, 5))
    def distance(first, second):
        return sum((a-b)**2 for a,b in zip(rgb(first), rgb(second))) ** .5
    if distance(base, highlight) >= 80:
        return highlight
    return '#FFE600' if distance(base, '#FFE600') >= 100 else '#25DFFF'


def singing_start(row):
    """Use the first supplied token onset, otherwise the sentence's own onset."""
    tokens = _tokens(row)
    return tokens[0][2] if tokens else row['startMs']


def traditional_lines(segments, maximum=14):
    """Display short phrases using supplied token onsets, without editing lyrics.

    Sentence-only lines and an oversized single token stay intact. Never split
    a token or invent a time just to fit a visual line.
    """
    result = []
    for row in segments:
        if _tokens(row) is None:
            result.append(row)
            continue
        groups, current, length = [], [], 0
        for token in row['characters']:
            if current and length + len(token['text']) > maximum:
                groups.append(current); current, length = [], 0
            current.append(token); length += len(token['text'])
            if length >= 5 and token['text'][-1] in '，。！？；,.!?;':
                groups.append(current); current, length = [], 0
        if current:
            groups.append(current)
        for index, tokens in enumerate(groups):
            result.append(dict(row, text=''.join(token['text'] for token in tokens),
                startMs=row['startMs'] if index == 0 else tokens[0]['startMs'],
                endMs=groups[index + 1][0]['startMs'] if index + 1 < len(groups) else row['endMs'],
                characters=tokens))
    return result


def lyric_frame(rows, position_ms, *, pre_display=False):
    """Select the same fixed slot from source time, including seeks and gaps."""
    if not _finite(position_ms):
        return None
    for index, row in enumerate(rows):
        active = row['startMs'] <= position_ms < row['endMs']
        if active or (pre_display and position_ms < row['startMs']):
            return {'current': row, 'following': rows[index + 1] if index + 1 < len(rows) else None,
                    'appearanceMs': rows[index - 2]['endMs'] if index >= 2 else 0,
                    'followingAppearanceMs': rows[index - 1]['endMs'] if index >= 1 else 0,
                    'lineIndex': index, 'positionMs': position_ms,
                    'waiting': pre_display and position_ms < singing_start(row)}
    return None
