"""Bounded AX clock pairing; fixtures are synthetic and not live evidence."""
import copy
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from probes.rekordbox_ax import (HELPER_APP, MAX_CAPTURE_NS, TTL_NS, RekordboxAXReader,
                                 empty_deck, parse_observations, run, status)
from probes.rekordbox_reader import ReaderError


def nodes(*, a='00:34', at='.5', ar='-03:23', art='.9', title_a='测试混音#',
          b='01:08', bt='.2', br='-02:50', brt='.2', title_b='测试另一版'):
    result = [{'role': 'AXPopUpButton', 'text': '2Deck Horizontal', 'x': 0, 'y': 0},
              {'role': 'AXPopUpButton', 'text': 'PERFORMANCE', 'x': 0, 'y': 0}]
    for x, title, remaining, remainder, elapsed, tenth in (
            (100, title_a, ar, art, a, at), (900, title_b, br, brt, b, bt)):
        for role, text in [('AXStaticText', title), ('AXImage', ''), ('AXStaticText', ''),
                ('AXStaticText', '140.00'), ('AXStaticText', 'Am'),
                ('AXStaticText', remaining), ('AXStaticText', remainder),
                ('AXStaticText', elapsed), ('AXStaticText', tenth)]:
            result.append({'role': role, 'text': text, 'x': x, 'y': 200})
    return result


class RekordboxAXTests(unittest.TestCase):
    def test_elapsed_and_remaining_fraction_pairing_are_independent_for_ab(self):
        result = parse_observations(nodes(), nodes())
        self.assertEqual([deck['deck'] for deck in result], ['A', 'B'])
        self.assertEqual(result[0]['positionSeconds'], 34.5)
        self.assertEqual(result[0]['remainingSeconds'], 203.9)
        self.assertEqual(result[1]['positionSeconds'], 68.2)
        self.assertEqual(result[1]['durationSeconds'], 238.4)
        self.assertTrue(all(deck['playRequested'] is False for deck in result))
        self.assertTrue(all('trackPath' not in deck for deck in result))

    def test_title_is_metadata_not_filename_or_shared_title_identity(self):
        value = nodes(title_a='同名 Remix', title_b='同名 Remix')
        result = parse_observations(value, value)
        self.assertEqual(result[0]['title'], result[1]['title'])
        self.assertNotEqual(result[0]['positionSeconds'], result[1]['positionSeconds'])

    def test_empty_deck_clears_only_that_deck_and_all_empty_is_connected_frame(self):
        one = nodes(a='00:00', at='.0', ar='-00:00', art='.0', title_a='Not Loaded.')
        result = parse_observations(one, one)
        self.assertEqual(result[0], empty_deck('A'))
        self.assertTrue(result[1]['available'])
        both = nodes(a='00:00', at='.0', ar='-00:00', art='.0', title_a='Not Loaded.',
                     b='00:00', bt='.0', br='-00:00', brt='.0', title_b='Not Loaded.')
        self.assertEqual(parse_observations(both, both), [empty_deck('A'), empty_deck('B')])

    def test_clock_can_cross_second_boundary_when_both_samples_are_consistent(self):
        result = parse_observations(nodes(a='00:34', at='.9', ar='-03:23', art='.5'),
                                    nodes(a='00:35', at='.0', ar='-03:23', art='.4'))
        self.assertEqual(result[0]['positionSeconds'], 35)

    def test_torn_fraction_second_pair_is_rejected(self):
        with self.assertRaisesRegex(ReaderError, 'inconsistent_ax_observation'):
            parse_observations(nodes(a='00:34', at='.0'), nodes(a='00:35', at='.0'))
        with self.assertRaisesRegex(ReaderError, 'inconsistent_ax_observation'):
            parse_observations(nodes(), nodes(ar='-03:20'))

    def test_mid_capture_title_change_is_rejected(self):
        with self.assertRaisesRegex(ReaderError, 'track_changed_during_capture'):
            parse_observations(nodes(), nodes(title_b='换歌了'))

    def test_missing_clock_fraction_empty_title_wrong_layout_are_unavailable(self):
        for values in (nodes(at=''), nodes(a='00:99'), nodes(title_a=''), nodes(br='02:50')):
            with self.subTest(values=values):
                with self.assertRaises(ReaderError):
                    parse_observations(values, values)
        value = nodes()
        value[0]['text'] = '4Deck Horizontal'
        with self.assertRaisesRegex(ReaderError, 'unsupported_layout'):
            parse_observations(value, value)

    def test_deck_horizontal_geometry_must_disambiguate_sides(self):
        values = nodes()
        for value in values:
            value['x'] = 0
        with self.assertRaisesRegex(ReaderError, 'unsupported_layout'):
            parse_observations(values, values)
        values = nodes()
        for value in values:
            if value['x'] == 900:
                value['y'] += 200
        with self.assertRaisesRegex(ReaderError, 'unsupported_layout'):
            parse_observations(values, values)

    def test_malformed_snapshots_and_nonfinite_geometry_are_rejected(self):
        for value in (None, [], [1], [{'role': 'AXStaticText', 'text': 1}], nodes() * 21):
            with self.subTest(value=value):
                with self.assertRaises(ReaderError):
                    parse_observations(value, value)
        value = nodes()
        value[7]['x'] = float('nan')
        with self.assertRaises(ReaderError):
            parse_observations(value, value)

    def reader(self, samples=None, stamps=None):
        values = iter(stamps or [1_000_000_000, 1_010_000_000] * 3)
        reader = RekordboxAXReader(clock=lambda: next(values))
        reader.profile = {'app': '/Applications/rekordbox 7/rekordbox.app'}
        reader.process = SimpleNamespace(stdin=SimpleNamespace(write=lambda _: None, flush=lambda: None))
        sample = {'ok': True, 'first': nodes(), 'second': nodes(), 'process': {'pid': 123}}
        samples = iter(samples or [sample] * 3)
        reader._line = lambda: copy.deepcopy(next(samples))
        return reader

    def test_frames_are_source_positions_without_external_extrapolation_or_identity(self):
        frame = self.reader().read_frame()
        self.assertEqual(frame['mode'], 'rekordbox_ax')
        self.assertEqual(frame['type'], 'rekordbox_ax_frame')
        self.assertEqual(frame['sourceKind'], 'accessibility')
        self.assertTrue(frame['epoch'].startswith('rekordbox-ax:'))
        self.assertEqual(frame['validUntilNs'] - frame['capturedNs'], TTL_NS)
        self.assertEqual(frame['diagnostics']['resolutionMs'], 100)
        self.assertIsNone(frame['fader'])
        self.assertNotIn('trackPath', frame['decks'][0])
        self.assertNotEqual(frame['decks'][0]['observationId'], frame['decks'][1]['observationId'])

    def test_observation_ids_stable_during_tenth_rounding_change_on_track_or_duration(self):
        samples = [{'ok': True, 'first': value, 'second': value} for value in
                   (nodes(), nodes(art='.8'), nodes(title_b='新曲目'))]
        reader = self.reader(samples)
        first, second, third = [reader.read_frame() for _ in range(3)]
        self.assertEqual(first['decks'][0]['observationId'], second['decks'][0]['observationId'])
        self.assertNotEqual(second['decks'][1]['observationId'], third['decks'][1]['observationId'])
        self.assertEqual([first['sequence'], second['sequence'], third['sequence']], [1, 2, 3])

    def test_slow_capture_and_native_status_publish_no_stale_frame(self):
        reader = self.reader(stamps=[1_000_000_000, 1_000_000_001 + MAX_CAPTURE_NS])
        with self.assertRaisesRegex(ReaderError, 'capture_timeout'):
            reader.read_frame()
        reader = self.reader([{'ok': False, 'code': 'window_minimized'}])
        with self.assertRaisesRegex(ReaderError, 'window_minimized'):
            reader.read_frame()
        message = status(ReaderError('window_minimized'))
        self.assertEqual(message['decks'], [empty_deck('A'), empty_deck('B')])
        self.assertFalse(message['producerVerified'])

    def test_once_native_permission_failure_stops_and_clears(self):
        stop = SimpleNamespace(is_set=lambda: False, wait=lambda _: self.fail('must stop once'))
        output = []
        class Denied:
            def __enter__(self):
                raise ReaderError('accessibility_permission_required')
            def __exit__(self, *_):
                pass
        run(stop, output.append, factory=Denied, once=True)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['diagnostics']['code'], 'accessibility_permission_required')
        self.assertIn('辅助功能', output[0]['detail'])

    def test_permission_prompt_is_explicit_opt_in_only(self):
        for requested in (False, True):
            with self.subTest(requested=requested):
                child = Mock()
                child.poll.return_value = 0
                reader = RekordboxAXReader(helper_builder=lambda: '/our/ax-helper',
                                           request_access=requested)
                reader._line = lambda **_: {'ok': True, 'ready': True}
                with patch('probes.rekordbox_ax.inspect_original', return_value={'binary': '/original'}), \
                        patch('probes.rekordbox_ax._find_pid', return_value=123), \
                        patch('probes.rekordbox_ax.selectors.DefaultSelector'), \
                        patch('probes.rekordbox_ax.subprocess.Popen', return_value=child) as spawn:
                    with reader:
                        pass
                    command = spawn.call_args.args[0]
                    self.assertEqual(command, ['/our/ax-helper', '123'] +
                                     (['--request-access'] if requested else []))
        with self.assertRaises(ValueError):
            RekordboxAXReader(request_access='yes')

    def test_application_host_uses_private_fifos_owner_identity_and_removes_session(self):
        for request_access in (False, True):
            with self.subTest(request_access=request_access), tempfile.TemporaryDirectory() as temporary:
                child = Mock()
                child.poll.return_value = None
                reader = RekordboxAXReader(helper_builder=lambda: '/our/ax-helper',
                    application_host=True, request_access=request_access)
                reader._line = lambda **_: {'ok': True, 'ready': True}
                with patch('probes.rekordbox_ax.RUNTIME', Path(temporary)), \
                        patch('probes.rekordbox_ax.SESSION_ROOT', Path(temporary)), \
                        patch('probes.rekordbox_ax.inspect_original', return_value={'binary': '/original'}), \
                        patch('probes.rekordbox_ax._find_pid', return_value=123), \
                        patch('probes.rekordbox_ax.selectors.DefaultSelector'), \
                        patch('probes.rekordbox_ax.subprocess.Popen', return_value=child) as spawn:
                    with reader:
                        session = reader.session
                        self.assertEqual(stat.S_IMODE(session.stat().st_mode), 0o700)
                        command = spawn.call_args.args[0]
                        self.assertEqual(command[:6], ['/usr/bin/open', '-n', '-g', '-W', '-a', str(HELPER_APP)])
                        for option in ('--stdin', '--stdout'):
                            path = Path(command[command.index(option) + 1])
                            self.assertTrue(stat.S_ISFIFO(path.stat().st_mode))
                            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                            self.assertEqual(path.parent, session)
                        self.assertEqual(command[command.index('--args') + 1:], ['123'] +
                            (['--request-access'] if request_access else []) + ['--owner-pid', str(os.getpid())])
                        self.assertEqual(stat.S_IMODE((session / 'helper.stderr.log').stat().st_mode), 0o600)
                    self.assertFalse(session.exists())
                    self.assertIsNone(reader.stdin)
                    self.assertIsNone(reader.stdout)
                    self.assertIsNone(reader.session)
                    child.wait.assert_called_once_with(timeout=2)

    def test_application_host_failed_launch_closes_fifos_and_removes_session(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch('probes.rekordbox_ax.RUNTIME', Path(temporary)), \
                patch('probes.rekordbox_ax.SESSION_ROOT', Path(temporary)), \
                patch('probes.rekordbox_ax.inspect_original', return_value={'binary': '/original'}), \
                patch('probes.rekordbox_ax._find_pid', return_value=123), \
                patch('probes.rekordbox_ax.subprocess.Popen', side_effect=OSError('launch rejected')):
            reader = RekordboxAXReader(helper_builder=lambda: '/our/ax-helper', application_host=True)
            with self.assertRaisesRegex(OSError, 'launch rejected'):
                reader.__enter__()
            self.assertEqual([p.name for p in Path(temporary).iterdir()], ['last-launch-error.json'])
            self.assertIsNone(reader.stdin)
            self.assertIsNone(reader.stdout)

    def test_handshake_failure_keeps_original_error_and_cleans_up_when_log_directory_is_missing_or_blocked(self):
        for blocked in ('missing', 'mkdir', 'write'):
            with self.subTest(blocked=blocked), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                runtime = directory / 'new-user-helper-data'
                if blocked == 'mkdir':
                    runtime.write_text('existing file prevents diagnostic directory creation')
                elif blocked == 'write':
                    (runtime / 'last-launch-error.json').mkdir(parents=True)
                child = Mock()
                child.poll.return_value = None
                original = ReaderError('accessibility_permission_required', 'original failure detail')
                reader = RekordboxAXReader(helper_builder=lambda: '/our/ax-helper', application_host=True)
                sessions = []
                def failed_handshake(**_):
                    sessions.append(reader.session)
                    raise original
                reader._line = failed_handshake
                with patch('probes.rekordbox_ax.RUNTIME', runtime), \
                        patch('probes.rekordbox_ax.SESSION_ROOT', directory), \
                        patch('probes.rekordbox_ax.inspect_original', return_value={'binary': '/original'}), \
                        patch('probes.rekordbox_ax._find_pid', return_value=123), \
                        patch('probes.rekordbox_ax.selectors.DefaultSelector'), \
                        patch('probes.rekordbox_ax.subprocess.Popen', return_value=child):
                    with self.assertRaises(ReaderError) as caught:
                        reader.__enter__()
                self.assertIs(caught.exception, original)
                self.assertEqual(original.detail, 'original failure detail')
                self.assertTrue(sessions)
                self.assertTrue(all(not session.exists() for session in sessions))
                for field in ('stdin', 'stdout', 'session', 'process', 'selector', 'launch_log'):
                    self.assertIsNone(getattr(reader, field), field)
                child.wait.assert_called_once_with(timeout=2)
                if blocked == 'missing':
                    self.assertTrue((runtime / 'last-launch-error.json').is_file())


if __name__ == '__main__':
    unittest.main()
