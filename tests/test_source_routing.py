import copy
import unittest

from app.source_routing import check_source_snapshot, select_lyric_source


def deck(version='sha256:' + 'a' * 64):
    return {'versionId': version, 'durationMs': 4000.0, 'positionMs': 1000.0,
            'speed': 1.0, 'playing': True, 'capturedNs': 1_000_000_000,
            'validUntilNs': 1_350_000_000}


def timeline(version='sha256:' + 'a' * 64):
    return {'schemaVersion': 1, 'versionId': version, 'durationMs': 4000,
            'quality': 'sentence', 'segments': [
                {'startMs': 500, 'endMs': 1500, 'text': '原创歌词'}]}


class SourceReadinessTests(unittest.TestCase):
    def check(self, source=None, lyrics=None, now=1_100_000_000, status=0, identity=True):
        return check_source_snapshot(source if source is not None else deck(),
                                     lyrics if lyrics is not None else timeline(), now,
                                     clock_status=status, identity_ready=identity)

    def test_explicit_identity_and_clock_attestations_required(self):
        self.assertFalse(check_source_snapshot(deck(), timeline(), 1_100_000_000)['ready'])
        self.assertEqual(self.check(identity=False)['reasonCode'], 'identity_unverified')
        self.assertEqual(self.check(status=None)['reasonCode'], 'clock_unavailable')
        self.assertEqual(self.check(status=True)['reasonCode'], 'clock_unavailable')
        self.assertEqual(self.check(status={})['reasonCode'], 'clock_unavailable')
        self.assertTrue(self.check()['ready'])

    def test_clock_status_and_ttl_boundaries_hide_without_extrapolation(self):
        for status, code in ((1, 'clock_stale'), (2, 'clock_invalid'), (3, 'clock_reverse'),
                             (99, 'clock_unavailable')):
            with self.subTest(status=status):
                self.assertEqual(self.check(status=status)['reasonCode'], code)
        self.assertTrue(self.check(now=1_000_000_000)['ready'])
        self.assertTrue(self.check(now=1_350_000_000)['ready'])
        self.assertEqual(self.check(now=1_350_000_001)['reasonCode'], 'clock_stale')
        self.assertEqual(self.check(now=999_999_999)['reasonCode'], 'clock_future')

    def test_new_track_cannot_reuse_old_timeline_or_identity(self):
        new = deck('sha256:' + 'b' * 64)
        self.assertEqual(self.check(source=new)['reasonCode'], 'lyrics_version_mismatch')
        self.assertEqual(self.check(source=new, identity=False)['reasonCode'], 'identity_unverified')
        self.assertTrue(self.check(source=new, lyrics=timeline(new['versionId']))['ready'])

    def test_missing_malformed_and_duration_mismatched_lyrics_hide(self):
        self.assertEqual(check_source_snapshot(deck(), None, 1_100_000_000,
                                               clock_status=0, identity_ready=True)['reasonCode'], 'lyrics_missing')
        bad = timeline()
        bad['segments'][0]['endMs'] = float('nan')
        self.assertEqual(self.check(lyrics=bad)['reasonCode'], 'lyrics_invalid')
        bad = timeline()
        bad['durationMs'] = 5000
        self.assertEqual(self.check(lyrics=bad)['reasonCode'], 'lyrics_invalid')

    def test_malformed_stamps_and_source_values_hide(self):
        cases = [('capturedNs', True), ('capturedNs', 0), ('capturedNs', 1 << 63),
                 ('validUntilNs', 999_999_999), ('validUntilNs', 2_000_000_001),
                 ('positionMs', -1), ('positionMs', float('nan')),
                 ('durationMs', -1), ('speed', float('inf')), ('playing', 'true')]
        for name, value in cases:
            source = deck()
            source[name] = value
            with self.subTest(name=name, value=value):
                self.assertFalse(self.check(source=source)['ready'])

    def test_pause_does_not_make_prepared_lyrics_unready_and_inputs_unchanged(self):
        source, lyrics = deck(), timeline()
        source['playing'] = False
        before = copy.deepcopy((source, lyrics))
        self.assertTrue(self.check(source=source, lyrics=lyrics)['ready'])
        self.assertEqual((source, lyrics), before)

    def test_unknown_duration_uses_verified_version_without_guessing_raw_length_units(self):
        source = deck()
        source['durationMs'] = 0
        source['songLengthRaw'] = 42
        self.assertTrue(self.check(source=source)['ready'])
        source['positionMs'] = 86_400_000
        self.assertTrue(self.check(source=source)['ready'])
        source['positionMs'] = 86_400_001
        self.assertEqual(self.check(source=source)['reasonCode'], 'clock_invalid')
        source['positionMs'] = 1000
        self.assertEqual(self.check(source=source, identity=False)['reasonCode'], 'identity_unverified')
        bad = timeline()
        bad['durationMs'] = 0
        self.assertEqual(self.check(source=source, lyrics=bad)['reasonCode'], 'lyrics_invalid')


class VideoRoutingTests(unittest.TestCase):
    def setUp(self):
        self.ready = {'A': {'ready': True}, 'B': {'ready': True}}

    def route(self, fader, previous='none', selected='auto', sources=None, **kwargs):
        return select_lyric_source(selected, fader, previous,
                                   self.ready if sources is None else sources, **kwargs)

    def test_boundaries_and_center_hold_have_exact_rules(self):
        for fader, previous, expected in ((0, 'none', 'A'), (.479999, 'B', 'A'),
                                         (.48, 'B', 'B'), (.5, 'A', 'A'),
                                         (.52, 'A', 'A'), (.520001, 'A', 'B'),
                                         (1, 'none', 'B')):
            with self.subTest(fader=fader, previous=previous):
                result = self.route(fader, previous)
                self.assertEqual(result['effectiveSource'], expected)
                self.assertEqual(result['autoSide'], expected)

    def test_first_centered_frame_does_not_guess_a_side(self):
        for fader in (.48, .5, .52):
            result = self.route(fader)
            self.assertEqual(result['effectiveSource'], 'none')
            self.assertEqual(result['reasonCode'], 'center_without_history')

    def test_hysteresis_prevents_center_jitter_and_epoch_reset_forgets_side(self):
        previous = 'none'
        for fader, expected in ((.4, 'A'), (.49, 'A'), (.51, 'A'), (.53, 'B'), (.49, 'B')):
            result = self.route(fader, previous)
            self.assertEqual(result['effectiveSource'], expected)
            previous = result['autoSide']
        self.assertEqual(self.route(.5, previous='none')['effectiveSource'], 'none')

    def test_manual_and_hidden_override_missing_video_route(self):
        for selected in ('A', 'B'):
            result = self.route(float('nan'), selected=selected)
            self.assertEqual(result['effectiveSource'], selected)
            self.assertEqual(result['selectionBasis'], 'manual')
        result = self.route(1, selected='none')
        self.assertEqual(result['effectiveSource'], 'none')
        self.assertEqual(result['reasonCode'], 'manual_hidden')

    def test_unready_chosen_side_hides_and_never_borrows_other_side(self):
        sources = {'A': {'ready': False, 'reasonCode': 'lyrics_missing', 'reason': '等待 A 歌词'},
                   'B': {'ready': True}}
        for selected in ('auto', 'A'):
            result = self.route(.2, selected=selected, sources=sources)
            self.assertEqual(result['requestedSource'], 'A')
            self.assertEqual(result['effectiveSource'], 'none')
            self.assertEqual(result['autoSide'], 'A')
            self.assertEqual(result['reasonCode'], 'lyrics_missing')
        sources['A']['ready'] = True
        self.assertEqual(self.route(.5, 'A', sources=sources)['effectiveSource'], 'A')

    def test_stale_selected_clock_hides_even_if_other_clock_is_fresh(self):
        sources = {'A': check_source_snapshot(deck(), timeline(), 1_350_000_001,
                                              clock_status=1, identity_ready=True),
                   'B': {'ready': True}}
        result = self.route(.2, sources=sources)
        self.assertEqual(result['effectiveSource'], 'none')
        self.assertEqual(result['reasonCode'], 'clock_stale')

    def test_reversed_video_mapping_and_manual_override_history(self):
        self.assertEqual(self.route(.2, left_source='B', right_source='A')['effectiveSource'], 'B')
        self.assertEqual(self.route(.8, left_source='B', right_source='A')['effectiveSource'], 'A')
        manual = self.route(.8, selected='A')
        self.assertEqual(manual['effectiveSource'], 'A')
        self.assertEqual(manual['autoSide'], 'B')
        self.assertEqual(self.route(.5, manual['autoSide'])['effectiveSource'], 'B')

    def test_unavailable_fader_mapping_and_selection_have_diagnostics(self):
        for fader in (None, float('nan'), float('inf'), True, -.1, 1.1, 10 ** 1000):
            with self.subTest(fader=fader):
                result = self.route(fader, previous='A')
                self.assertEqual(result['effectiveSource'], 'none')
                self.assertEqual(result['reasonCode'], 'video_route_unavailable')
        self.assertEqual(self.route(.2, video_fader_ready=False)['effectiveSource'], 'none')
        self.assertEqual(self.route(.2, left_source='A', right_source='A')['effectiveSource'], 'none')
        self.assertEqual(self.route(.2, selected='unknown')['reasonCode'], 'selection_unavailable')

    def test_diagnostic_never_claims_audio_vocal_selection(self):
        result = self.route(.8)
        self.assertEqual(result['diagnosticSource'], 'video-fader:B')
        self.assertEqual(result['audioVocalSource'], 'unverified')
        self.assertIn('音频人声来源未核验', result['reason'])


if __name__ == '__main__':
    unittest.main()
