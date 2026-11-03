import copy
import unittest

from app.composition import (build_concert_composition, edit_clip, empty_composition,
                             make_text, refresh_concert_coverage, remove_clip,
                             split_clip, validate_composition)
from tests.test_composition import source
from worker.concert import concert_coverage, plan_concert_ranges


SOURCE = [{'start': 1, 'end': 3, 'text': '唱起这首歌'},
          {'start': 4, 'end': 6, 'text': '永远在一起'}]
TARGET = [{'start': 2, 'end': 4, 'text': '唱起这首歌'},
          {'start': 5, 'end': 7, 'text': '永远在一起'},
          {'start': 8, 'end': 10, 'text': '唱起这首歌'},
          {'start': 11, 'end': 13, 'text': '完全无关的词'}]


def base():
    value = empty_composition()
    value['sources'] = {'concert': source(duration=8000),
                        'filler': source(duration=700, path='/tmp/filler.mp4'),
                        'photo': source('image', path='/tmp/filler.png')}
    value['texts'] = [make_text('title', 0, 15000)]
    return value


class ConcertTests(unittest.TestCase):
    def test_repeat_exact_chorus_and_unmatched_cutaways_complete_range(self):
        plan = plan_concert_ranges(SOURCE, TARGET, 8000, 15000)
        paired = [row for row in plan['ranges'] if row['purpose'] == 'paired']
        self.assertEqual(len(paired), 3)
        repeated = paired[-1]
        self.assertEqual((repeated['startMs'], repeated['sourceInMs'], repeated['rate']), (8000, 1000, 1))
        self.assertIn('重复', repeated['reason'])
        self.assertEqual(plan['repeatedCandidates'][0]['targetIndices'], [2])
        self.assertTrue(any(row['purpose'] == 'cutaway' for row in plan['ranges']))
        self.assertEqual(sum(row['durationMs'] for row in plan['ranges']), 15000)
        self.assertEqual(plan['ranges'][0]['startMs'], 0)
        self.assertFalse(plan['acousticTimingVerified'])
        self.assertFalse(plan['visualSafetyVerified'])

    def test_speed_outside_candidate_range_fills_instead_of_overrun(self):
        plan = plan_concert_ranges([{'start': 0, 'end': 7, 'text': '同一句歌词'}],
                                  [{'start': 1, 'end': 2, 'text': '同一句歌词'}], 8000, 3000)
        self.assertFalse(any(row['purpose'] == 'paired' for row in plan['ranges']))
        self.assertTrue(any('速度' in row['reason'] for row in plan['ranges']))

    def test_source_and_target_seconds_strictly_bounded(self):
        for source_rows, target_rows in (
                ([{'start': 1, 'end': 8.01, 'text': '超出源'}], TARGET),
                (SOURCE, [{'start': 14, 'end': 15.01, 'text': '超出目标'}]),
                ([{'start': float('nan'), 'end': 3, 'text': '无效'}], TARGET)):
            with self.subTest(source=source_rows), self.assertRaises(ValueError):
                plan_concert_ranges(source_rows, target_rows, 8000, 15000)

    def test_multiple_identical_source_candidates_disclosed(self):
        rows = [{'start': 0, 'end': 1, 'text': '同一句'},
                {'start': 2, 'end': 3, 'text': '同一句'}]
        target = [{'start': index * 2, 'end': index * 2 + 1, 'text': '同一句'} for index in range(3)]
        plan = plan_concert_ranges(rows, target, 4000, 6000)
        self.assertEqual(len([row for row in plan['ranges'] if row['purpose'] == 'paired']), 3)
        self.assertEqual(plan['repeatedCandidates'][0]['alternatives'], 2)
        self.assertTrue(any('多个' in row['reason'] for row in plan['ranges']))

    def test_materialized_finite_video_filler_and_complete_coverage(self):
        original = base()
        result = build_concert_composition(original, 'concert', SOURCE, TARGET, 15000, 'filler')
        coverage = result['concert']['coverage']
        self.assertEqual(coverage['coveredMs'], 15000)
        self.assertEqual(coverage['pairedMs'], 6000)
        self.assertEqual(coverage['cutawayMs'], 2000)
        self.assertEqual(coverage['gapMs'], 7000)
        self.assertEqual(coverage['coverageRatio'], 1)
        self.assertTrue(coverage['fullCoverage'])
        self.assertFalse(coverage['reviewed'])
        self.assertEqual(coverage['reviewRequiredRows'], len(result['clips']))
        for row in result['clips']:
            media = result['sources'][row['sourceId']]
            self.assertLessEqual(row['sourceInMs'] + row['durationMs'] * row['rate'], media['durationMs'] + 1e-6)
        self.assertEqual(result['texts'], original['texts'])
        self.assertEqual(original['clips'], [])

    def test_color_and_image_fallback(self):
        for filler in ('', 'photo'):
            plan = build_concert_composition(base(), 'concert', SOURCE, TARGET, 15000, filler)
            gaps = [row for row in plan['clips'] if row['purpose'] != 'paired']
            self.assertTrue(all(row['kind'] == ('color' if not filler else 'image') for row in gaps))
            self.assertEqual(plan['concert']['coverage']['coveredMs'], 15000)

    def test_review_edit_and_split_invalidate_only_changed_rows(self):
        original = build_concert_composition(base(), 'concert', SOURCE, TARGET, 15000, reviewed=True)
        self.assertTrue(original['concert']['reviewed'])
        paired = next(row for row in original['clips'] if row['purpose'] == 'paired')
        result = edit_clip(original, paired['id'], {'sourceInMs': 2000, 'rate': .5}, 15000)
        self.assertFalse(result['concert']['reviewed'])
        self.assertEqual(result['concert']['coverage']['reviewRequiredRows'], 1)
        self.assertTrue(original['concert']['reviewed'])
        result = edit_clip(result, paired['id'], {'reviewed': True}, 15000)
        self.assertTrue(result['concert']['reviewed'])
        split = split_clip(result, paired['id'], paired['startMs'] + 1000, 15000)
        self.assertEqual(split['concert']['coverage']['reviewRequiredRows'], 2)
        self.assertEqual(split['concert']['coverage']['coveredMs'], 15000)
        with self.assertRaises(ValueError):
            edit_clip(original, paired['id'], {'sourceInMs': 99999}, 15000)
        with self.assertRaises(ValueError):
            edit_clip(original, paired['id'], {'rate': .5, 'reviewed': True}, 15000)

    def test_removed_candidate_cannot_keep_complete_review(self):
        value = build_concert_composition(base(), 'concert', SOURCE, TARGET, 15000, reviewed=True)
        removed = value['clips'][0]
        result = remove_clip(value, removed['id'], 15000)
        self.assertFalse(result['concert']['reviewed'])
        self.assertFalse(result['concert']['coverage']['fullCoverage'])
        self.assertEqual(result['concert']['coverage']['coveredMs'], 15000 - removed['durationMs'])

    def test_candidate_metadata_strict_and_statistics_cannot_be_forged(self):
        original = build_concert_composition(base(), 'concert', SOURCE, TARGET, 15000)
        for changes in ({'purpose': 'certified'}, {'reviewed': 'yes'}, {'reason': object()}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                edit_clip(original, original['clips'][0]['id'], changes, 15000)
        forged = copy.deepcopy(original)
        forged['concert']['coverage']['reviewed'] = True
        with self.assertRaises(ValueError):
            validate_composition(forged, 15000)
        for key, change in [('acousticTimingVerified', True), ('visualSafetyVerified', True),
                            ('targetDigest', 'a' * 64)]:
            forged = copy.deepcopy(original)
            forged['concert'][key] = change
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_composition(forged, 15000)
        original['clips'][0]['reviewed'] = True
        refreshed = refresh_concert_coverage(original, 15000)
        self.assertEqual(refreshed['concert']['coverage']['reviewRequiredRows'], len(original['clips']) - 1)

    def test_preparation_state_lyrics_identity_preserved_and_checked(self):
        value = base()
        identity = source(path='/tmp/selected.srt')['identity']
        value['concert'] = {'sourceId': 'concert', 'sourceSegments': SOURCE, 'reviewed': False,
                            'lyricsPath': '/tmp/selected.srt', 'lyricsIdentity': identity, 'gapSourceId': ''}
        validate_composition(value, 15000)
        result = build_concert_composition(value, 'concert', SOURCE, TARGET, 15000)
        self.assertEqual(result['concert']['lyricsIdentity'], identity)
        self.assertEqual(result['concert']['lyricsPath'], '/tmp/selected.srt')
        value['concert']['lyricsPath'] = '/tmp/different.srt'
        with self.assertRaises(ValueError):
            validate_composition(value, 15000)
        value['concert'] = {'sourceId': 'concert', 'sourceSegments': [], 'lyricsPath': '', 'reviewed': False}
        validate_composition(value, 15000)
        value['concert']['reviewed'] = True
        with self.assertRaises(ValueError):
            validate_composition(value, 15000)

    def test_coverage_rejects_overlaps_nonfinite_and_invalid_purpose(self):
        clips = [{'startMs': 0, 'durationMs': 1000}, {'startMs': 500, 'durationMs': 1000}]
        with self.assertRaises(ValueError):
            concert_coverage(clips, 2000)
        for invalid in ({'startMs': 0, 'durationMs': float('nan')},
                        {'startMs': 0, 'durationMs': 1000, 'purpose': 'bad'}):
            with self.assertRaises(ValueError):
                concert_coverage([invalid], 2000)


if __name__ == '__main__':
    unittest.main()
