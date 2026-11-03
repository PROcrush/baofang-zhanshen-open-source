import copy
import hashlib
import unittest

from app.storage import validate_timeline
from worker.correction import correct_with_reference


VERSION = 'sha256:' + 'a' * 64


def timeline(texts, quality='word'):
    segments = []
    for index, text in enumerate(texts):
        start = 1000 + index * 2000
        end = start + 1000
        segment = {'text': text, 'startMs': start, 'endMs': end}
        if quality == 'word':
            # Two provider tokens leave an observed pause in the middle.
            split = max(1, len(text) // 2)
            segment['words'] = [{'text': text[:split], 'startMs': start, 'endMs': start + 400},
                                {'text': text[split:], 'startMs': start + 600, 'endMs': end}]
        segments.append(segment)
    return {'schemaVersion': 1, 'versionId': VERSION, 'durationMs': len(texts) * 2000 + 1000,
            'quality': quality, 'timingProvenance': 'provider_token' if quality == 'word' else 'provider_sentence',
            'acousticAccuracyVerified': False, 'segments': segments}


class ReferenceCorrectionTests(unittest.TestCase):
    def test_unique_one_character_typo_keeps_actual_token_intervals_and_audit(self):
        source = timeline(['往事随疯'])
        before = copy.deepcopy(source)
        result = correct_with_reference(source, '往事随风', reference_label='用户提供的原歌词')
        self.assertEqual(result['segments'][0]['text'], '往事随风')
        self.assertEqual(result['quality'], 'word')
        self.assertEqual([word['text'] for word in result['segments'][0]['words']], ['往事', '随风'])
        self.assertEqual([(word['startMs'], word['endMs']) for word in result['segments'][0]['words']],
                         [(1000, 1400), (1600, 2000)])
        self.assertEqual(result['timingProvenance'], 'corrected_provider_token')
        self.assertEqual(result['segments'][0]['originalText'], '往事随疯')
        self.assertEqual(result['correction']['changes'][0]['originalText'], '往事随疯')
        self.assertEqual(result['correction']['referenceLabel'], '用户提供的原歌词')
        self.assertEqual(result['correction']['referenceSha256'], hashlib.sha256('往事随风'.encode('utf-8')).hexdigest())
        self.assertFalse(result['correction']['timingRealigned'])
        self.assertFalse(result['acousticAccuracyVerified'])
        self.assertEqual(source, before)

    def test_length_change_downgrades_entire_timeline_without_inventing_word_times(self):
        source = timeline(['今晚跟着节奏起', '把新的画面交给这一刻'])
        result = correct_with_reference(source, '今晚跟着节奏起舞\n把新的画面交给这一刻')
        self.assertEqual(result['quality'], 'sentence')
        self.assertEqual(result['segments'][0]['text'], '今晚跟着节奏起舞')
        self.assertTrue(all('words' not in row for row in result['segments']))
        self.assertEqual([(row['startMs'], row['endMs']) for row in result['segments']],
                         [(row['startMs'], row['endMs']) for row in source['segments']])
        self.assertEqual(result['correction']['changes'][0]['timingAction'], 'downgraded_to_sentence')
        self.assertIn('originalWords', result['correction']['changes'][0])
        validate_timeline(result, VERSION)

    def test_duplicate_reference_chorus_is_not_forced_into_one_occurrence(self):
        source = timeline(['往事随疯'])
        result = correct_with_reference(source, '往事随风\n往事随风')
        self.assertEqual(result['segments'][0]['text'], '往事随疯')
        self.assertEqual(result['correction']['status'], 'needs_review')
        self.assertEqual(result['correction']['review'][0]['reasonCode'], 'ambiguous_reference')
        self.assertEqual(result['correction']['changes'], [])

    def test_similar_competing_lines_require_review(self):
        result = correct_with_reference(timeline(['今晚的节奏由我们接菅']),
                                        '今晚的节奏由我们接管\n今晚的节奏由他们接管')
        self.assertEqual(result['segments'][0]['text'], '今晚的节奏由我们接菅')
        self.assertEqual(result['correction']['review'][0]['reasonCode'], 'ambiguous_reference')

    def test_mashup_reverse_order_keeps_asr_instead_of_reordering_reference(self):
        source = timeline(['把新的画面交给这一刻', '今晚跟着节奏起菅'])
        result = correct_with_reference(source, '今晚跟着节奏起舞\n把新的画面交给这一刻')
        self.assertEqual(result['segments'][1]['text'], source['segments'][1]['text'])
        self.assertEqual(result['correction']['review'][0]['reasonCode'], 'reference_order_mismatch')
        self.assertEqual(result['correction']['changes'], [])

    def test_unknown_mashup_passages_remain_asr_while_clear_reference_line_can_correct(self):
        source = timeline(['往事随疯', '这一段喊麦原曲参考没有'])
        result = correct_with_reference(source, '往事随风')
        self.assertEqual(result['segments'][0]['text'], '往事随风')
        self.assertEqual(result['segments'][1]['text'], source['segments'][1]['text'])
        self.assertEqual(result['correction']['status'], 'needs_review')
        self.assertEqual(result['correction']['review'][0]['reasonCode'], 'no_clear_match')

    def test_short_fragment_is_not_corrected_by_a_guess(self):
        result = correct_with_reference(timeline(['想你'], quality='sentence'), '爱你')
        self.assertEqual(result['segments'][0]['text'], '想你')
        self.assertEqual(result['correction']['status'], 'needs_review')

    def test_exact_text_and_punctuation_only_are_unchanged(self):
        source = timeline(['往事随风'])
        result = correct_with_reference(source, '\ufeff\n往事随风。\n')
        self.assertEqual(result['segments'], source['segments'])
        self.assertEqual(result['correction']['status'], 'unchanged')
        self.assertEqual(result['timingProvenance'], 'provider_token')

    def test_empty_user_reference_is_reported_without_claiming_automatic_search(self):
        source = timeline(['往事随疯'])
        result = correct_with_reference(source, '')
        self.assertEqual(result['segments'], source['segments'])
        self.assertEqual(result['correction']['status'], 'needs_review')
        self.assertEqual(result['correction']['automaticSearch'], 'unavailable')

    def test_bad_input_timestamps_and_invalid_reference_are_rejected(self):
        for value in (float('nan'), -1, 2000):
            source = timeline(['往事随疯'])
            source['segments'][0]['startMs'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                correct_with_reference(source, '往事随风')
        for reference in (None, ['往事随风'], '长' * 501):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                correct_with_reference(timeline(['往事随疯']), reference)


if __name__ == '__main__':
    unittest.main()
