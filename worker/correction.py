"""Conservative user-reference corrections, retaining source-media timing.

This first adapter accepts explicit reference text only. It performs no search,
network request, recognition, or acoustic alignment. Reused provider intervals
remain inherited intervals, including when the replacement has equal length.
"""
import copy
import difflib
import hashlib
import unicodedata

from app.storage import validate_timeline


MIN_SIMILARITY = .86
AMBIGUITY_MARGIN = .12


def _normalized(text):
    return ''.join(char.casefold() for char in text
                   if not char.isspace() and not unicodedata.category(char).startswith('P'))


def _reference_rows(text):
    if not isinstance(text, str) or len(text) > 1_000_000:
        raise ValueError('参考歌词必须是大小合理的文本')
    rows = []
    for number, line in enumerate(text.removeprefix('\ufeff').splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if len(line) > 500:
            raise ValueError('参考歌词单行过长')
        normalized = _normalized(line)
        if normalized:
            rows.append((number, line, normalized))
    if len(rows) > 1000:
        raise ValueError('参考歌词行数过多')
    return rows


def _score(left, right):
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    if matcher.real_quick_ratio() < .75 or matcher.quick_ratio() < .75:
        return 0.0
    return matcher.ratio()


def _high_similarity(left, right, score):
    if left == right:
        return True
    if min(len(left), len(right)) < 4:
        return False
    if score >= MIN_SIMILARITY:
        return True
    # A unique three-of-four character match can correct a single typo, but
    # short fragments, deletions, and ambiguous repetitions never qualify.
    return len(left) == len(right) and sum(a != b for a, b in zip(left, right)) == 1


def correct_with_reference(timeline, reference_text, *, reference_label='用户提供参考文本'):
    """Return a corrected copy with provenance, changes, and unresolved rows.

    Best reference matches must be unique, high similarity, and strictly
    increasing in reference order. Duplicate/close candidates and unmatched
    mashup passages keep their ASR text. Equal code-point counts map text onto
    the existing token spans; any changed count downgrades the whole output to
    sentence quality. No intervals are synthesized or shifted.
    """
    if not isinstance(timeline, dict) or not isinstance(timeline.get('versionId'), str):
        raise ValueError('纠错需要已绑定源版本的歌词时间轴')
    try:
        validate_timeline(timeline, timeline['versionId'])
    except (TypeError, KeyError, OverflowError) as exc:
        raise ValueError('纠错输入时间轴无效') from exc
    if not isinstance(reference_label, str) or not reference_label.strip() or len(reference_label) > 500:
        raise ValueError('参考出处无效')
    rows = _reference_rows(reference_text)
    result = copy.deepcopy(timeline)
    changes, review = [], []
    cursor = -1
    downgrade = False

    for index, segment in enumerate(result['segments']):
        original = segment['text']
        normalized = _normalized(original)
        candidates = sorted(((_score(normalized, row[2]), row_index)
                             for row_index, row in enumerate(rows)), reverse=True)
        if not candidates or not normalized:
            review.append({'segmentIndex': index, 'reasonCode': 'reference_unavailable', 'originalText': original})
            continue
        score, row_index = candidates[0]
        reference_line, replacement, target = rows[row_index]
        if not _high_similarity(normalized, target, score):
            review.append({'segmentIndex': index, 'reasonCode': 'no_clear_match',
                           'originalText': original, 'bestSimilarity': round(score, 6)})
            continue
        if len(candidates) > 1 and score - candidates[1][0] <= AMBIGUITY_MARGIN:
            review.append({'segmentIndex': index, 'reasonCode': 'ambiguous_reference',
                           'originalText': original, 'bestSimilarity': round(score, 6)})
            continue
        if row_index <= cursor:
            review.append({'segmentIndex': index, 'reasonCode': 'reference_order_mismatch',
                           'originalText': original, 'referenceLine': reference_line})
            continue
        cursor = row_index
        # Whitespace and punctuation differences alone do not justify changing
        # timing quality or replacing already correct recognized characters.
        if normalized == target:
            continue

        change = {'segmentIndex': index, 'referenceLine': reference_line,
                  'referenceSource': 'user_provided', 'referenceLabel': reference_label,
                  'originalText': original, 'correctedText': replacement,
                  'similarity': round(score, 6), 'startMs': segment['startMs'],
                  'endMs': segment['endMs'], 'timingAction': 'sentence_times_retained'}
        segment['originalText'] = segment.get('originalText', original)
        segment['text'] = replacement
        if timeline['quality'] == 'word':
            change['originalWords'] = copy.deepcopy(segment['words'])
            if len(original) == len(replacement):
                offset = 0
                for word in segment['words']:
                    length = len(word['text'])
                    word['text'] = replacement[offset:offset + length]
                    offset += length
                change['timingAction'] = 'provider_token_times_retained'
                segment['timingProvenance'] = 'corrected_provider_token'
            else:
                downgrade = True
                change['timingAction'] = 'downgraded_to_sentence'
        segment['correctionProvenance'] = {
            key: value for key, value in change.items() if key != 'originalWords'}
        changes.append(change)

    if downgrade:
        result['quality'] = 'sentence'
        for change in changes:
            if change['timingAction'] == 'provider_token_times_retained':
                change['timingAction'] = 'downgraded_to_sentence'
                result['segments'][change['segmentIndex']]['correctionProvenance']['timingAction'] = 'downgraded_to_sentence'
        for segment in result['segments']:
            segment.pop('words', None)
            if segment.get('timingProvenance') == 'corrected_provider_token':
                segment['timingProvenance'] = 'corrected_provider_sentence'
        result['timingProvenance'] = 'corrected_provider_sentence'
    elif changes and result['quality'] == 'word':
        result['timingProvenance'] = 'corrected_provider_token'
    elif changes:
        result['timingProvenance'] = 'corrected_provider_sentence'
    if changes:
        result['acousticAccuracyVerified'] = False
    result['correction'] = {
        'status': 'needs_review' if review else ('applied' if changes else 'unchanged'),
        'referenceSource': 'user_provided', 'referenceLabel': reference_label,
        'referenceSha256': hashlib.sha256(reference_text.encode('utf-8')).hexdigest(),
        'referenceLineCount': len(rows),
        'automaticSearch': 'unavailable', 'changes': changes, 'review': review,
        'originalQuality': timeline['quality'],
        'originalTimingProvenance': timeline.get('timingProvenance'),
        'timingRealigned': False,
    }
    return validate_timeline(result, result['versionId'])
