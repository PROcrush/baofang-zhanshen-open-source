import copy
import unittest

from app.composition import (add_marker, edit_audio_clip, edit_clip, edit_text,
                             empty_composition, frame_snap_ms, generate_storyboard,
                             make_audio_clip, make_clip, make_text, remove_audio_clip,
                             remove_clip, remove_marker, remove_text, set_clip_locked,
                             split_clip, validate_composition)


def source(kind='video', duration=10000, path='/tmp/selected.mp4', **extra):
    return {'identity': {'path': path, 'size': 10, 'mtimeNs': 2, 'device': 1,
                         'inode': 3, 'sha256': 'a' * 64}, 'kind': kind,
            'durationMs': 0 if kind == 'image' else duration,
            'width': 0 if kind == 'audio' else 320,
            'height': 0 if kind == 'audio' else 180, **extra}


def composition():
    value = empty_composition()
    value['sources'] = {'video': source(), 'photo': source('image', path='/tmp/photo.png'),
                        'sound': source('audio', path='/tmp/sound.wav')}
    value['clips'] = [make_clip('video', 'video', 0, 2000, id='first', sourceInMs=1000, rate=2),
                      make_clip('photo', 'image', 2000, 2000, id='second',
                                transition={'id': 'fade-left', 'durationMs': 250})]
    return value


class CompositionTests(unittest.TestCase):
    def test_empty_preparation_accepts_zero_clock_but_no_timeline_rows(self):
        value = empty_composition()
        self.assertEqual(value['fps'], 24)
        value['sources']['video'] = source()
        value['concert'] = {'sourceId': 'video', 'sourceSegments': [], 'reviewed': False}
        self.assertEqual(validate_composition(value, 0), value)
        for key, row in [('clips', make_clip('', 'color', 0, 1)),
                         ('texts', make_text('title', 0, 1)),
                         ('audioClips', make_audio_clip('video', 0, 1)),
                         ('markers', {'id': 'marker', 'positionMs': 0, 'label': '起点'})]:
            invalid = copy.deepcopy(value)
            invalid[key] = [row]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_composition(invalid, 0)

    def test_validator_copies_and_callback_gets_identity(self):
        original = composition()
        seen = []
        result = validate_composition(original, 6000, lambda identity: seen.append(identity))
        self.assertEqual(len(seen), 3)
        result['clips'][0]['startMs'] = 99
        seen[0]['size'] = 99
        self.assertEqual(original['clips'][0]['startMs'], 0)
        self.assertEqual(original['sources']['video']['identity']['size'], 10)
        with self.assertRaises(ValueError):
            validate_composition(original, 6000, lambda identity: {**identity, 'size': 11})

    def test_nonfinite_and_unexpected_fields_rejected_without_mutation(self):
        original = composition()
        saved = copy.deepcopy(original)
        for changes in ({'rate': float('nan')}, {'startMs': float('inf')},
                        {'durationMs': True}, {'startMs': 10 ** 400}, {'unknown': 1},
                        {'transition': {'id': 'fade-left', 'durationMs': 250, 'extra': 1}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                edit_clip(original, 'first', changes, 6000)
            self.assertEqual(original, saved)
        invalid = copy.deepcopy(original)
        invalid['sources']['video']['durationMs'] = float('nan')
        with self.assertRaises(ValueError):
            validate_composition(invalid, 6000)

    def test_strict_source_identity_and_clock(self):
        original = composition()
        for key, value in [('path', 'relative.mp4'), ('sha256', 'x' * 64), ('size', True)]:
            invalid = copy.deepcopy(original)
            invalid['sources']['video']['identity'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_composition(invalid, 6000)
        for changes in ({'sourceInMs': 8000}, {'rate': 5}, {'sourceId': 'sound'}, {'kind': 'image'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                edit_clip(original, 'first', changes, 6000)

    def test_trim_move_rate_and_order(self):
        original = composition()
        result = edit_clip(original, 'first', {'startMs': 4000, 'durationMs': 1000,
                                              'sourceInMs': 7000, 'rate': 3}, 6000)
        self.assertEqual([clip['id'] for clip in result['clips']], ['second', 'first'])
        self.assertEqual(result['clips'][0]['transition'], {'id': 'cut', 'durationMs': 0})
        self.assertEqual(result['clips'][1]['sourceInMs'] + 1000 * 3, 10000)
        with self.assertRaises(ValueError):
            edit_clip(original, 'first', {'startMs': 1500}, 6000)

    def test_split_preserves_exact_source_clock_and_repairs_transition(self):
        original = composition()
        result = split_clip(original, 'first', 1250, 6000)
        left, right, next_clip = result['clips']
        self.assertEqual((left['startMs'], left['durationMs'], left['sourceInMs']), (0, 1250, 1000))
        self.assertEqual((right['startMs'], right['durationMs'], right['sourceInMs']), (1250, 750, 3500))
        self.assertEqual(right['rate'], 2)
        self.assertEqual(right['transition']['id'], 'cut')
        self.assertEqual(next_clip['transition']['durationMs'], 250)
        self.assertEqual(original['clips'][0]['durationMs'], 2000)
        with self.assertRaises(ValueError):
            split_clip(original, 'first', 2000, 6000)

    def test_remove_leaves_gap_and_resets_incoming_transition(self):
        original = composition()
        result = remove_clip(original, 'first', 6000)
        self.assertEqual(result['clips'][0]['startMs'], 2000)
        self.assertEqual(result['clips'][0]['transition'], {'id': 'cut', 'durationMs': 0})
        self.assertEqual(len(original['clips']), 2)

    def test_locked_prevents_edits_split_remove_and_collateral_transition_change(self):
        original = set_clip_locked(composition(), 'first', True, 6000)
        for action in (lambda: edit_clip(original, 'first', {'sourceInMs': 0}, 6000),
                       lambda: split_clip(original, 'first', 500, 6000),
                       lambda: remove_clip(original, 'first', 6000),
                       lambda: generate_storyboard(original, [], 6000)):
            with self.assertRaises(ValueError):
                action()
        unlocked = set_clip_locked(original, 'first', False, 6000)
        self.assertFalse(unlocked['clips'][0]['locked'])
        locked_next = set_clip_locked(composition(), 'second', True, 6000)
        with self.assertRaises(ValueError):
            remove_clip(locked_next, 'first', 6000)

    def test_frame_transition_constraints_and_clamping(self):
        original = composition()
        result = edit_clip(original, 'second', {'transition': {'id': 'wipe-up', 'durationMs': 999}}, 6000)
        self.assertAlmostEqual(result['clips'][1]['transition']['durationMs'], 23 * 1000 / 24)
        result = edit_clip(original, 'second', {'transition': {'id': 'wipe-up', 'durationMs': 2000}}, 6000)
        self.assertEqual(result['clips'][1]['transition']['durationMs'], 1000)
        self.assertEqual(frame_snap_ms(250, 24), 250)
        for change in ({'durationMs': 251}, {'durationMs': 1200}):
            invalid = copy.deepcopy(original)
            invalid['clips'][1]['transition'].update(change)
            with self.assertRaises(ValueError):
                validate_composition(invalid, 6000)
        with self.assertRaises(ValueError):
            edit_clip(original, 'first', {'transition': {'id': 'fade-left', 'durationMs': 250}}, 6000)
        invalid = copy.deepcopy(original)
        invalid['clips'][1]['startMs'] += 1
        with self.assertRaises(ValueError):
            validate_composition(invalid, 6000)

    def test_text_audio_markers_are_independent_and_bounded(self):
        original = composition()
        original['texts'] = [make_text('歌手名字', 1000, 2000, id='title', layoutId='title')]
        original['audioClips'] = [make_audio_clip('sound', 1000, 2000, id='fx', sourceInMs=2000, rate=2)]
        original = validate_composition(original, 6000)
        value = edit_text(original, 'title', {'x': .8, 'opacity': .25}, 6000)
        value = edit_audio_clip(value, 'fx', {'gain': .3}, 6000)
        value = add_marker(value, 2500, '副歌', 6000)
        self.assertEqual(value['clips'], original['clips'])
        self.assertEqual(value['audioClips'][0]['gain'], .3)
        self.assertEqual(value['texts'][0]['x'], .8)
        value = remove_marker(value, value['markers'][0]['id'], 6000)
        value = remove_audio_clip(value, 'fx', 6000)
        value = remove_text(value, 'title', 6000)
        self.assertEqual((value['texts'], value['audioClips'], value['markers']), ([], [], []))
        for changes in ({'x': 2}, {'fontSize': 500}, {'text': ''}, {'color': 'red'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                edit_text(original, 'title', changes, 6000)
        for changes in ({'gain': 3}, {'sourceInMs': 9000}, {'rate': float('nan')}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                edit_audio_clip(original, 'fx', changes, 6000)

    def test_video_audio_track_and_has_audio(self):
        value = composition()
        value['sources']['video']['hasAudio'] = True
        value['audioClips'] = [make_audio_clip('video', 0, 1000)]
        validate_composition(value, 6000)
        value['sources']['video']['hasAudio'] = False
        with self.assertRaises(ValueError):
            validate_composition(value, 6000)

    def test_text_concurrency_end_before_start(self):
        value = empty_composition()
        value['texts'] = [make_text(str(index), 0, 1000) for index in range(12)]
        value['texts'].append(make_text('next', 1000, 1000))
        validate_composition(value, 2000)
        value['texts'].append(make_text('too many', 999, 500))
        with self.assertRaises(ValueError):
            validate_composition(value, 2000)

    def test_storyboard_frame_boundaries_media_tiling_and_tracks_preserved(self):
        value = composition()
        value['sources']['video']['durationMs'] = 700
        value['clips'] = []
        value['texts'] = [make_text('title', 0, 6000)]
        value['markers'] = [{'id': 'mark', 'positionMs': 500, 'label': '起点'}]
        lyrics = [{'startMs': 513, 'endMs': 2200, 'text': '第一句'},
                  {'startMs': 2900, 'endMs': 5100, 'text': '第二句'}]
        result = generate_storyboard(value, lyrics, 6000, ['video'], 'fade-left')
        clips = result['clips']
        self.assertEqual(clips[0]['startMs'], 0)
        self.assertAlmostEqual(clips[-1]['startMs'] + clips[-1]['durationMs'], 6000)
        self.assertAlmostEqual(sum(clip['durationMs'] for clip in clips), 6000)
        self.assertTrue(all(clip['durationMs'] <= 700 + 1e-6 for clip in clips))
        self.assertEqual(result['texts'], value['texts'])
        self.assertEqual(result['markers'], value['markers'])
        self.assertTrue(any(clip['transition']['id'] == 'fade-left' for clip in clips))
        self.assertEqual(value['clips'], [])

    def test_storyboard_color_fallback_empty_lyrics_and_invalid_cues(self):
        result = generate_storyboard(empty_composition(), [], 2300)
        self.assertEqual(len(result['clips']), 1)
        self.assertEqual(result['clips'][0]['kind'], 'color')
        with self.assertRaises(ValueError):
            generate_storyboard(empty_composition(), [{'start': 1, 'end': 3, 'text': '超出'}], 2300)
        with self.assertRaises(ValueError):
            generate_storyboard(composition(), [], 6000, ['sound'])

    def test_duplicate_ids_and_unknown_top_fields(self):
        value = composition()
        value['texts'] = [make_text('title', 0, 1000, id='first')]
        with self.assertRaises(ValueError):
            validate_composition(value, 6000)
        value = composition()
        value['secretState'] = {}
        with self.assertRaises(ValueError):
            validate_composition(value, 6000)


if __name__ == '__main__':
    unittest.main()
