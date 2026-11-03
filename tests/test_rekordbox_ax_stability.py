"""Reader-boundary regressions for torn AX clocks and false file unbinding."""
import copy
from types import SimpleNamespace
import unittest

from probes.rekordbox_ax import RekordboxAXReader, TTL_NS
from probes.rekordbox_reader import ReaderError
from tests.test_rekordbox_ax import nodes


def sample(first=None, second=None, cost=10_000_000):
    first = nodes() if first is None else first
    return cost, {'ok': True, 'first': first, 'second': first if second is None else second}


class ReaderStabilityTests(unittest.TestCase):
    def reader(self, replies):
        clock = SimpleNamespace(ns=1_000_000_000)
        reader = RekordboxAXReader(clock=lambda: clock.ns)
        reader.profile = {'app': '/Applications/rekordbox 7/rekordbox.app'}
        reader.process = SimpleNamespace(stdin=SimpleNamespace(write=lambda _: None, flush=lambda: None))
        pending = iter(replies)
        def line(**_):
            cost, response = next(pending)
            clock.ns += cost
            return copy.deepcopy(response)
        reader._line = line
        return reader, clock

    def ids(self, frame):
        return {item['deck']: item['observationId'] for item in frame['decks']}

    def test_torn_clock_recovers_with_fresh_read_before_publishing_failure(self):
        torn = sample(nodes(a='00:34', at='.0'), nodes(a='00:35', at='.0'))
        reader, _ = self.reader([sample(), torn, sample(nodes(a='00:35', at='.0', ar='-03:23', art='.4'))])
        first = reader.read_frame()
        recovered = reader.read_frame()
        self.assertEqual(recovered['decks'][0]['positionSeconds'], 35)
        self.assertEqual(self.ids(first), self.ids(recovered))
        self.assertLessEqual(recovered['diagnostics']['captureNs'], 200_000_000)

    def test_transient_missing_fields_retry_does_not_unbind_unchanged_files(self):
        reader, _ = self.reader([sample(), (10_000_000, {'ok': False, 'code': 'ax_fields_unavailable'}), sample()])
        first = reader.read_frame()
        self.assertEqual(self.ids(first), self.ids(reader.read_frame()))

    def test_over_budget_frame_is_rejected_without_revoking_fresh_confirmation(self):
        reader, _ = self.reader([sample(), sample(cost=201_000_000), sample()])
        first = reader.read_frame()
        with self.assertRaisesRegex(ReaderError, 'capture_timeout'):
            reader.read_frame()
        self.assertEqual(self.ids(first), self.ids(reader.read_frame()))

    def test_loading_b_during_capture_does_not_revoke_unchanged_a(self):
        changed = nodes(title_b='第二首实际歌曲')
        reader, _ = self.reader([sample(), sample(nodes(), changed), sample(changed)])
        first = reader.read_frame()
        later = reader.read_frame()
        self.assertEqual(self.ids(first)['A'], self.ids(later)['A'])
        self.assertNotEqual(self.ids(first)['B'], self.ids(later)['B'])

    def test_blind_interval_beyond_recovery_window_requires_confirmation_even_if_title_matches(self):
        reader, clock = self.reader([sample(), sample()])
        first = reader.read_frame()
        clock.ns += 2_000_000_001
        self.assertNotEqual(self.ids(first), self.ids(reader.read_frame()))

    def test_changed_track_still_invalidates_only_the_changed_side(self):
        reader, _ = self.reader([sample(), sample(nodes(title_b='不同文件'))])
        first = reader.read_frame()
        later = reader.read_frame()
        self.assertEqual(self.ids(first)['A'], self.ids(later)['A'])
        self.assertNotEqual(self.ids(first)['B'], self.ids(later)['B'])

    def test_one_consistent_but_torn_total_duration_is_not_a_new_track(self):
        # Both native scalar passes can see the same UI refresh half-state.
        torn = nodes(a='00:35', at='.0', ar='-03:23', art='.9')
        settled = nodes(a='00:35', at='.0', ar='-03:23', art='.4')
        reader, _ = self.reader([sample(), sample(torn), sample(settled)])
        first = reader.read_frame()
        later = reader.read_frame()
        self.assertEqual(later['decks'][0]['durationSeconds'], 238.4)
        self.assertEqual(self.ids(first), self.ids(later))

    def test_real_same_title_different_length_still_requires_new_confirmation(self):
        changed = nodes(a='00:35', at='.0', ar='-03:25', art='.0')
        reader, _ = self.reader([sample(), sample(changed), sample(changed)])
        first = reader.read_frame()
        later = reader.read_frame()
        self.assertNotEqual(self.ids(first)['A'], self.ids(later)['A'])
        self.assertEqual(self.ids(first)['B'], self.ids(later)['B'])

    def test_retries_share_one_capture_budget_and_do_not_publish_old_clocks(self):
        failure = (110_000_000, {'ok': False, 'code': 'ax_fields_unavailable'})
        reader, _ = self.reader([sample(), failure, sample(cost=100_000_000)])
        reader.read_frame()
        with self.assertRaisesRegex(ReaderError, 'capture_timeout') as raised:
            reader.read_frame()
        self.assertEqual(raised.exception.ax_diagnostics['readAttempts'], 2)
        self.assertEqual(reader.sequence, 1)

    def test_repeated_clock_race_stops_after_three_attempts(self):
        torn = sample(nodes(a='00:34'), nodes(a='00:36'))
        reader, _ = self.reader([sample(), torn, torn, torn])
        reader.read_frame()
        with self.assertRaisesRegex(ReaderError, 'inconsistent_ax_observation') as raised:
            reader.read_frame()
        self.assertEqual(raised.exception.ax_diagnostics['readAttempts'], 3)
        self.assertEqual(reader.sequence, 1)

    def test_permission_loss_still_immediately_revokes_all_observation_ids(self):
        reader, _ = self.reader([sample(), (10_000_000,
            {'ok': False, 'code': 'accessibility_permission_required'})])
        reader.read_frame()
        with self.assertRaisesRegex(ReaderError, 'accessibility_permission_required') as raised:
            reader.read_frame()
        self.assertEqual(reader.observed, {})
        self.assertEqual({c['deck'] for c in raised.exception.ax_diagnostics['identityChanges']}, {'A', 'B'})

    def test_one_window_enumeration_race_does_not_clear_both_confirmations(self):
        # Observed on the original app after 275 seconds of continuous play.
        reader, _ = self.reader([sample(), (10_000_000,
            {'ok': False, 'code': 'window_not_unique'}), sample()])
        first = reader.read_frame()
        later = reader.read_frame()
        self.assertEqual(self.ids(first), self.ids(later))
        self.assertEqual(later['diagnostics']['recoveryCodes'], ['window_not_unique'])

    def test_short_blind_interval_hides_old_clock_but_retains_file_confirmation(self):
        reader, clock = self.reader([sample(), sample()])
        first = reader.read_frame()
        clock.ns += 700_000_000
        self.assertGreater(clock.ns, first['validUntilNs'])
        later = reader.read_frame()
        self.assertEqual(self.ids(first), self.ids(later))
        self.assertEqual(later['validUntilNs'] - later['capturedNs'], TTL_NS)

    def test_identity_reason_survives_later_frames_for_gui_diagnostics(self):
        changed = nodes(title_b='实际新歌曲')
        reader, _ = self.reader([sample(), sample(changed), sample(changed)])
        reader.read_frame()
        reader.read_frame()
        later = reader.read_frame()
        self.assertEqual(later['diagnostics']['recentIdentityChanges'][0]['reason'], 'track_metadata_changed')


if __name__ == '__main__':
    unittest.main()
