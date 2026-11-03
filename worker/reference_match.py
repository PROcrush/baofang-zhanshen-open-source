"""Text/pinyin anchors against reference phrases, never against original LRC time."""
import copy
import hashlib
import re
from difflib import SequenceMatcher
from functools import lru_cache

from pypinyin import lazy_pinyin

from worker.lyric_reference import normalized, validate_reference


@lru_cache(maxsize=4096)
def _pinyin(text):
    return tuple(lazy_pinyin(text, errors=lambda value: list(value)))


def _score(source, target):
    text = SequenceMatcher(None, source, target, autojunk=False).ratio()
    sound = SequenceMatcher(None, _pinyin(source), _pinyin(target), autojunk=False).ratio()
    return .55 * text + .45 * sound


def _windows(lines):
    seen = set()
    for start in range(len(lines)):
        for length in range(1, 4):
            span = lines[start:start + length]
            if len(span) != length:
                continue
            text = normalized(''.join(span))
            if text in seen or not text:
                continue
            seen.add(text)
            yield {'text': text, 'lineStart': start + 1, 'lineEnd': start + length, 'referenceText': ' / '.join(span)}


def _anchor(text, windows):
    if len(text) < 4:
        return None, '短采样保留识别原文'
    # Exact substrings explain cropped vocals without inventing their missing tail.
    exact = [dict(row, score=1., partial=text != row['text']) for row in windows if text in row['text']]
    if exact:
        return min(exact, key=lambda row: len(row['text'])), ''
    ranked = []
    for row in windows:
        target = row['text']
        if not .8 <= len(target) / len(text) <= 1.25:
            continue
        score = _score(text, target)
        if score >= .70:
            ranked.append(dict(row, score=score, partial=False))
    ranked.sort(key=lambda row: row['score'], reverse=True)
    if not ranked or ranked[0]['score'] < .86:
        return None, '没有清楚的原版歌词锚点，保留识别原文'
    if len(ranked) > 1 and ranked[0]['score'] - ranked[1]['score'] < .07:
        return None, '存在相近的参考句，需人工核对'
    best = ranked[0]
    # Short near-matches are too easy to confuse with spoken words or samples.
    if len(text) < 6 or (len(text) != len(best['text']) and best['score'] < .92):
        return None, '片段较短或缺字，保留识别原文'
    return best, ''


def _replace_characters(raw, replacement):
    positions = [i for i, c in enumerate(raw) if normalized(c)]
    if len(positions) == len(replacement):
        letters = list(raw)
        for i, value in zip(positions, replacement):
            letters[i] = value
        return ''.join(letters)
    # Preserve punctuation at the edge; changed length requires listening/re-alignment.
    return raw[:positions[0]] + replacement + raw[positions[-1] + 1:]


def _correct_row(row, text):
    result = copy.deepcopy(row)
    result['text'] = text
    field = 'characters' if row.get('characters') else 'words' if row.get('words') else None
    words = row.get(field, []) if field else []
    if words and len(row['text']) == len(text) and ''.join(word['text'] for word in words) == row['text']:
        offset = 0
        for word in result[field]:
            length = len(word['text'])
            word['text'] = text[offset:offset + length]
            offset += length
        result['timingQuality'] = 'word'
        return result, '保留全部识别字词时码，仅替换对应文字'
    result.pop('characters', None)
    result.pop('words', None)
    result['timingQuality'] = 'sentence'
    return result, '保留实际句时码；改字数后字词位置需重新对齐'


def match(rows, value):
    reference = validate_reference(value)
    if not isinstance(rows, list) or len(rows) > 500 or sum(len(row.get('text', '')) for row in rows) > 30_000:
        raise ValueError('识别歌词过长，请先分段处理')
    windows = list(_windows(reference['lines']))
    proposed, changes, unresolved = copy.deepcopy(rows), [], []
    matched = total = matched_rows = 0
    for index, row in enumerate(rows):
        before = row['text']
        parts = re.split(r'([，。！？；,.!?;\n]+)', before)
        anchors, problems, row_matched = [], [], 0
        whole=normalized(before)
        whole_anchor,_=_anchor(whole,windows)
        if whole_anchor:
            total+=len(whole)
            matched+=len(whole)
            row_matched=len(whole)
            if whole_anchor['partial']:
                problems.append('原版只匹配到片段，保留实际截断文字')
            else:
                anchors.append(whole_anchor)
                if whole!=whole_anchor['text']:
                    parts=[_replace_characters(before,whole_anchor['text'])]
        for part_index in ([] if whole_anchor else range(0, len(parts), 2)):
            text = normalized(parts[part_index])
            total += len(text)
            anchor, reason = _anchor(text, windows)
            if not anchor:
                if text:
                    problems.append(reason)
                continue
            matched += len(text)
            row_matched += len(text)
            if anchor['partial']:
                problems.append('原版只匹配到片段，保留实际截断文字')
                continue
            anchors.append(anchor)
            if text != anchor['text']:
                parts[part_index] = _replace_characters(parts[part_index], anchor['text'])
        if row_matched:
            matched_rows += 1
        after = ''.join(parts)
        if after != before:
            proposed[index], timing = _correct_row(row, after)
            same_length = len(before) == len(after)
            changes.append({'index': index, 'before': before, 'after': after, 'selected': same_length,
                            'score': round(min(anchor['score'] for anchor in anchors), 3),
                            'referenceText': ' / '.join(anchor['referenceText'] for anchor in anchors),
                            'referenceLines': [[anchor['lineStart'], anchor['lineEnd']] for anchor in anchors],
                            'timingNote': timing, 'timingPreserved': same_length,
                            'originalWords': copy.deepcopy(row.get('characters', row.get('words', []))),
                            'startMs': row['startMs'], 'endMs': row['endMs']})
        if problems:
            unresolved.append({'index': index, 'text': before, 'reason': '；'.join(dict.fromkeys(problems))})
    metadata = {key: value for key, value in reference.items() if key != 'lines'}
    coverage = round(matched / total, 3) if total else 0
    return {'rows': proposed, 'changes': changes, 'unresolved': unresolved, 'reference': metadata,
            'coverage': coverage, 'matchedRows': matched_rows, 'totalRows': len(rows),
            'method': 'reference_text_pinyin', 'originalClockUsed': False}


def rank(rows, candidates, title, artist=''):
    evaluated = []
    for value in candidates:
        proposal = match(rows, value)
        exact_title = normalized(title) == normalized(value['title'])
        artist_matches = not artist or normalized(artist) in normalized(value['artist'])
        score = proposal['coverage'] * .8 + (.15 if exact_title else 0) + (.05 if artist_matches else 0)
        evaluated.append((score, value, proposal, exact_title and artist_matches))
    evaluated.sort(key=lambda item: item[0], reverse=True)
    cards = []
    for score, value, proposal, exact in evaluated:
        cards.append({**value, 'coverage': proposal['coverage'], 'matchedRows': proposal['matchedRows'],
                      'totalRows': len(rows), 'score': round(score, 3)})
    # Equivalent chorus repetitions / covers must not create false ambiguity.
    distinct = []
    for entry in evaluated:
        if not entry[3]:
            continue
        signature = hashlib.sha256('\n'.join(sorted(set(normalized(line) for line in entry[1]['lines']))).encode()).hexdigest()
        if signature not in [item[0] for item in distinct]:
            distinct.append((signature, entry))
    automatic = bool(evaluated and evaluated[0][3] and evaluated[0][2]['coverage'] >= .65
                     and (len(distinct) < 2 or distinct[0][1][0] - distinct[1][1][0] >= .08))
    if any(card['matchedRows'] for card in cards):
        cards=[card for card in cards if card['matchedRows']]
    return cards, evaluated[0][2] if automatic else None
