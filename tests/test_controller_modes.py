"""Controller lifecycle/IPC regression checks without GUI or external apps."""
import copy
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QProcess

from app.controller import Controller, FileController, initial_state, project_state
from app.messages import read_program_payload, validate_program_payload
from app.native import NativeClock
from app.serato_adapter import adapt_serato_frame, filename_lyric_versions, proof_is_current
from app.storage import Library, inspect_audio


NOW = 1_000_000_000


def replay():
    decks = {key: {'versionId': f'demo:{key}', 'title': f'原创同步样例 {key}', 'path': '',
                   'positionMs': 1250, 'durationMs': 4000, 'speed': 1,
                   'playing': key == 'A', 'capturedNs': NOW,
                   'validUntilNs': NOW + 350_000_000, 'generation': 0}
             for key in ('A', 'B')}
    return {'mode': 'replay', 'protocolVersion': 1, 'epoch': 'replay:1', 'sequence': 1,
            'activeDeck': 'A', 'playing': True, 'speed': 1, 'loopEnabled': False, 'decks': decks}


def live_frame():
    return {'type': 'serato_frame', 'mode': 'serato', 'protocolVersion': 1,
            'epoch': 'serato:2', 'sequence': 1, 'producerVerified': True,
            'capturedNs': NOW, 'validUntilNs': NOW + 350_000_000,
            'decks': [{'deck_index': i, 'source_identity': i + 1,
                       'audio_path': '', 'position_seconds': 1.25, 'velocity': 1}
                      for i in (0, 1)],
            'fader': {'normalized': .5, 'left_deck_index': 0, 'right_deck_index': 1}}


def timeline():
    return {'schemaVersion': 1, 'versionId': 'demo:A', 'durationMs': 4000,
            'quality': 'sentence', 'segments': [{'startMs': 1000, 'endMs': 2000, 'text': '原创歌词'}]}


class FakeSignal:
    def emit(self):
        pass


class FakeBridge:
    def __init__(self):
        self.lines = []
        self.writes = []
        self.current_state = QProcess.ProcessState.Running
        self.arguments = []
        self.on_wait = None

    def queue(self, message):
        self.lines.append((json.dumps(message) + '\n').encode())

    def canReadLine(self):
        return bool(self.lines)

    def readLine(self):
        return self.lines.pop(0)

    def readAllStandardOutput(self):
        data = b''.join(self.lines)
        self.lines.clear()
        return data

    def readAllStandardError(self):
        return b''

    def state(self):
        return self.current_state

    def isOpen(self):
        return True

    def write(self, data):
        self.writes.append(bytes(data))

    def terminate(self):
        self.current_state = QProcess.ProcessState.NotRunning

    def waitForFinished(self, _):
        if self.on_wait:
            self.on_wait()
        return True

    def kill(self):
        self.current_state = QProcess.ProcessState.NotRunning

    def setProgram(self, value):
        self.program = value

    def setArguments(self, values):
        self.arguments = list(values)

    def start(self):
        self.current_state = QProcess.ProcessState.Running


class ControllerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.root = Path(__file__).resolve().parents[1]
        self.harness = SimpleNamespace(
            _state=initial_state(), raw={}, timelines={'demo:A': timeline()},
            bridge=FakeBridge(), stateChanged=FakeSignal(), runtime=self.runtime,
            clock=NativeClock(self.root), output=None, file_proofs={}, file_errors={}, _serato_lyric_versions={},
            index_pending={}, failed_file_signatures={}, notices=[], indexed=[])
        self.harness.notice = lambda value: self.harness.notices.append(value)
        self.harness.index_audio = lambda path: self.harness.indexed.append(path)
        # tick now refreshes preparation; use its safe manager-absent path here.
        for name in ('send', 'read_bridge', 'stopBridge', 'start_bridge', 'tick', 'setInputMode', 'connectSerato',
                     'refresh_preparation'):
            setattr(self.harness, name, MethodType(getattr(Controller, name), self.harness))

    def tearDown(self):
        self.temp.cleanup()

    def test_live_mode_controls_never_write_to_reader_stdin(self):
        self.harness._state['mode'] = 'serato'
        for method, arguments in (('togglePlayback', ()), ('seek', (5.0,)), ('setSpeed', (1.1,)),
                                  ('setDeck', ('B',)), ('toggleLoop', ()), ('simulateDisconnect', ()),
                                  ('loadTrack', ('demo:A', 'A'))):
            getattr(Controller, method)(self.harness, *arguments)
        self.assertEqual(self.harness.bridge.writes, [])

    def test_stop_acceptance_gate_also_blocks_commands_while_process_is_still_running(self):
        self.harness._state['bridgeRunning'] = False
        self.harness.send({'type': 'seek', 'seconds': 10})
        self.assertEqual(self.harness.bridge.writes, [])

    def test_serato_reuses_old_lyrics_by_filename_without_indexing_or_choosing_a_duplicate(self):
        harness = self.harness
        harness._state.update(mode='serato', inputMode='serato', selectedSource='A')
        source = self.runtime / 'set' / '原创 Remix.mp3'
        source.parent.mkdir()
        source.write_bytes(b'audio with updated play count')
        current = inspect_audio(source)
        old_version = 'sha256:' + 'a' * 64
        old = dict(current, versionId=old_version)
        harness.timelines[old_version] = dict(timeline(), versionId=old_version)
        harness.timelines[current['versionId']] = dict(
            timeline(), versionId=current['versionId'], timingProvenance='local_asr_sentence')
        frame = live_frame()
        frame['decks'][0]['audio_path'] = str(source)

        def observe(tracks):
            bindings = {row['path']: row['versionId'] for row in reversed(tracks)}
            harness._serato_lyric_versions = filename_lyric_versions(bindings, tracks, harness.timelines)
            frame['sequence'] += 1
            harness.bridge.queue(frame)
            harness.read_bridge()
            with patch('app.controller.monotonic_ns', return_value=NOW):
                harness.tick()
            payload = read_program_payload(self.runtime / 'program.json')
            return project_state(payload['state'], payload['raw'], payload['timelines'], harness.clock, NOW)

        projected = observe([current, old])
        self.assertEqual(projected['effectiveSource'], 'A')
        self.assertEqual(projected['deckA']['versionId'], old_version)
        self.assertTrue(projected['lyric']['visible'])
        self.assertFalse(harness.raw['A']['identityReady'])
        self.assertEqual(harness.indexed, [])
        fallback = observe([current])
        self.assertEqual(fallback['effectiveSource'], 'A')
        self.assertEqual(fallback['deckA']['lyricSource'], '本地识别')
        self.assertTrue(fallback['deckA']['needsReview'])

        frame['decks'][0]['audio_path'] = str(self.runtime / 'moved' / source.name)
        self.assertEqual(observe([current, old])['deckA']['versionId'], old_version)

        frame['decks'][1]['audio_path'] = str(source)
        projected = observe([current, old])
        self.assertEqual(projected['selection']['reasonCode'], 'lyrics_ambiguous')
        self.assertEqual([harness.raw[key]['lyricVersionId'] for key in ('A', 'B')], [None, None])
        frame['decks'][0]['audio_path'] = str(source)
        self.assertEqual(observe([current, old])['deckA']['versionId'], old_version)
        frame['decks'][1]['audio_path'] = ''

        duplicate = dict(old, path=str(self.runtime / 'other' / source.name), versionId='sha256:' + 'b' * 64)
        projected = observe([current, old, duplicate])
        self.assertEqual(projected['effectiveSource'], 'none')
        self.assertEqual(projected['selection']['reasonCode'], 'lyrics_ambiguous')

        frame['decks'][0]['audio_path'] = str(source.with_suffix('.wav'))
        projected = observe([old])
        self.assertEqual(projected['selection']['reasonCode'], 'lyrics_missing')
        self.assertEqual(harness.indexed, [])
        with patch('app.controller.monotonic_ns', return_value=NOW):
            Controller.verifySeratoPreparation(harness, 'A')
        self.assertEqual(harness.indexed, [str(source.with_suffix('.wav'))])

    def test_serato_checks_all_bound_paths_even_when_their_version_is_identical(self):
        library = Library(self.runtime / 'library')
        self.addCleanup(library.close)
        paths = [self.runtime / folder / '同名.mp3' for folder in ('one', 'two')]
        for path in paths:
            path.parent.mkdir()
            path.write_bytes(b'identical audio')
            library.add_track(inspect_audio(path))
        tracks = library.tracks()
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(library.bindings()), 2)
        version = tracks[0]['versionId']
        timelines = {version: dict(timeline(), versionId=version)}
        self.assertEqual(filename_lyric_versions(library.bindings(), tracks, timelines), {'同名.mp3': None})

    def test_stop_drops_buffered_frames_before_wait_and_keeps_lyrics_hidden(self):
        self.harness.bridge.queue(replay())
        self.harness.bridge.on_wait = self.harness.read_bridge
        self.harness.stopBridge()
        self.harness.bridge.queue(replay())
        self.harness.read_bridge()
        with patch('app.controller.monotonic_ns', return_value=NOW):
            self.harness.tick()
        self.assertEqual(self.harness.raw, {})
        self.assertFalse(self.harness._state['lyric']['visible'])
        self.assertEqual(self.harness._state['effectiveSource'], 'none')

    def test_switch_to_live_drains_old_replay_and_rejects_late_replay_frames(self):
        self.harness.bridge.queue(replay())
        with patch('app.controller.monotonic_ns', return_value=NOW):
            self.harness.setInputMode('serato')
        self.assertEqual(self.harness.bridge.arguments, ['-m', 'probes.serato_reader'])
        self.harness.bridge.queue(replay())
        self.harness.read_bridge()
        self.assertEqual(self.harness.raw, {})
        self.assertEqual(self.harness._state['mode'], 'serato')
        self.assertFalse(self.harness._state['lyric']['visible'])
        self.assertEqual(self.harness.bridge.writes, [])

    def test_reconnect_running_or_stopped_reader_discards_old_observations_and_restarts(self):
        h = self.harness
        for process_state in (QProcess.ProcessState.Running, QProcess.ProcessState.Starting,
                              QProcess.ProcessState.NotRunning):
            with self.subTest(process_state=process_state):
                h._state.update(mode='serato', inputMode='serato', selectedSource='none', resumeSource='B',
                                bridgeEpoch='old:epoch', bridgeSequence=99, autoSide='B',
                                videoFader=.9, faderValidUntilNs=NOW+350_000_000,
                                lyricOffsets={'demo:B': 200}, outputOpen=True)
                h.raw = replay()['decks']
                h.bridge.current_state = process_state
                h.bridge.queue(live_frame())
                with patch('app.controller.monotonic_ns', return_value=NOW), \
                        patch.object(h.bridge, 'start', wraps=h.bridge.start) as start:
                    h.connectSerato()
                self.assertEqual(start.call_count, 1)
                self.assertEqual(h.bridge.arguments, ['-m', 'probes.serato_reader'])
                self.assertEqual(h.bridge.lines, [])
                self.assertEqual(h.raw, {})
                self.assertIsNone(h._state['bridgeEpoch'])
                self.assertEqual(h._state['bridgeSequence'], 0)
                self.assertEqual(h._state['videoFader'], -1)
                self.assertTrue(h._state['stale'])
                self.assertFalse(h._state['lyric']['visible'])
                self.assertEqual(h._state['selectedSource'], 'none')
                self.assertEqual(h._state['resumeSource'], 'B')
                self.assertEqual(h._state['lyricOffsets'], {'demo:B': 200})
                self.assertTrue(h._state['outputOpen'])
                self.assertIn('重新连接', h._state['bridgeStatus'])
                self.assertEqual(json.loads((h.runtime / 'program.json').read_text())['raw'], {})
                fresh = live_frame()
                fresh.update(epoch='serato:new', sequence=1)
                h.bridge.queue(fresh)
                h.read_bridge()
                self.assertEqual(h._state['bridgeEpoch'], 'serato:new')
                self.assertIn('已只读连接', h._state['bridgeStatus'])
        self.assertEqual(h.bridge.writes, [])

    def test_reconnect_does_not_start_over_a_reader_that_cannot_stop(self):
        h = self.harness
        h._state['mode'] = 'serato'
        with patch.object(h.bridge, 'terminate'), patch.object(h.bridge, 'kill'), \
                patch.object(h.bridge, 'waitForFinished', return_value=False), \
                patch.object(h.bridge, 'start') as start, \
                patch('app.controller.monotonic_ns', return_value=NOW):
            h.connectSerato()
        start.assert_not_called()
        self.assertFalse(h._state['bridgeRunning'])
        self.assertFalse(h._state['lyric']['visible'])
        self.assertIn('未能停止', h._state['bridgeStatus'])

    def test_connect_from_replay_switches_mode_and_drains_old_frames(self):
        self.harness.bridge.queue(replay())
        with patch('app.controller.monotonic_ns', return_value=NOW):
            self.harness.connectSerato()
        self.assertEqual(self.harness._state['mode'], 'serato')
        self.assertEqual(self.harness.bridge.arguments, ['-m', 'probes.serato_reader'])
        self.assertEqual(self.harness.raw, {})
        self.assertEqual(self.harness.bridge.writes, [])

    def test_malformed_complete_snapshot_preserves_previous_raw_epoch_and_route(self):
        self.harness.bridge.queue(replay())
        self.harness.read_bridge()
        before = copy.deepcopy(self.harness.raw)
        malformed = replay()
        malformed['sequence'] = 2
        malformed['decks']['B']['speed'] = float('nan')
        self.harness.bridge.queue(malformed)
        self.harness.read_bridge()
        self.assertEqual(self.harness.raw, before)
        self.assertEqual(self.harness._state['bridgeSequence'], 1)
        self.assertEqual(self.harness._state['bridgeEpoch'], 'replay:1')

    def test_adapter_rejection_does_not_clear_previous_epoch_hysteresis(self):
        self.harness._state.update(mode='serato', autoSide='B', bridgeEpoch='serato:1',
                                   bridgeSequence=20, videoFader=.5)
        self.harness.raw = {'sentinel': 'old snapshot'}
        self.harness.bridge.queue(live_frame())
        with patch('app.controller.adapt_serato_frame', side_effect=ValueError('incomplete proof')):
            self.harness.read_bridge()
        self.assertEqual(self.harness.raw, {'sentinel': 'old snapshot'})
        self.assertEqual(self.harness._state['bridgeEpoch'], 'serato:1')
        self.assertEqual(self.harness._state['autoSide'], 'B')

    def test_rejected_live_frame_reports_consumer_error_and_recovers_on_valid_frame(self):
        self.harness._state.update(mode='serato', bridgeStatus='两份快照不一致')
        malformed = live_frame()
        malformed['decks'][1]['audio_path'] = None
        self.harness.bridge.queue(malformed)
        self.harness.read_bridge()
        self.assertEqual(self.harness.raw, {})
        self.assertIn('接收校验', self.harness._state['bridgeStatus'])
        self.assertIn('audio_path', self.harness._state['bridgeStatus'])
        self.assertEqual(self.harness._state['readerDiagnostics']['consumerStatus'], 'rejected')
        self.harness.bridge.queue(live_frame())
        self.harness.read_bridge()
        self.assertEqual(set(self.harness.raw), {'A', 'B'})
        self.assertIn('已只读连接', self.harness._state['bridgeStatus'])
        self.assertNotIn('consumerStatus', self.harness._state['readerDiagnostics'])

    def test_index_queue_failure_does_not_leave_new_raw_with_old_frame_epoch(self):
        sample = live_frame()
        sample['decks'][0]['audio_path'] = str(self.runtime / 'missing.wav')
        self.harness._state.update(mode='serato', bridgeEpoch='serato:1', bridgeSequence=20,
                                   videoFader=0)
        self.harness.index_audio = lambda _: (_ for _ in ()).throw(ValueError('queue unavailable'))
        self.harness.bridge.queue(sample)
        self.harness.read_bridge()
        # A failure in optional indexing may either preserve the old snapshot
        # or accept the complete new one, but cannot mix the two generations.
        if self.harness.raw:
            self.assertEqual(self.harness._state['bridgeEpoch'], sample['epoch'])
            self.assertEqual(self.harness._state['bridgeSequence'], sample['sequence'])
            self.assertEqual(self.harness._state['videoFader'], sample['fader']['normalized'])

    def test_program_file_is_replaced_only_after_complete_valid_json_is_written(self):
        self.harness.bridge.queue(replay())
        self.harness.read_bridge()
        target = self.runtime / 'program.json'
        target.write_text('{"old":true}')
        original_replace = os.replace
        inspected = []

        def observe_replace(source, destination):
            if Path(destination) != target:
                original_replace(source, destination)
                return
            self.assertEqual(json.loads(target.read_text()), {'old': True})
            decoded = json.loads(Path(source).read_text())
            validate_program_payload(decoded)
            self.assertEqual(set(decoded['raw']), {'A', 'B'})
            inspected.append(decoded)
            original_replace(source, destination)

        with patch('app.controller.monotonic_ns', return_value=NOW), \
                patch('app.controller.os.replace', side_effect=observe_replace):
            self.harness.tick()
        self.assertEqual(len(inspected), 1)
        self.assertEqual(json.loads(target.read_text()), inspected[0])
        self.assertFalse((self.runtime / 'program.tmp').exists())

    def test_program_reuses_one_validated_sidecar_until_timeline_changes(self):
        self.harness.bridge.queue(replay())
        self.harness.read_bridge()
        target = self.runtime / 'program.json'
        cache = {}
        with patch('app.controller.monotonic_ns', return_value=NOW):
            self.harness.tick()
            original_ref = json.loads(target.read_text())['timelineRef']
            previous = read_program_payload(target, cache)['timelines']
            self.harness.tick()
            self.assertEqual(json.loads(target.read_text())['timelineRef'], original_ref)
            self.assertIs(read_program_payload(target, cache)['timelines'], previous)
            self.harness.timelines['demo:A'] = timeline()
            self.harness.timelines['demo:A']['segments'][0]['text'] = '更新歌词'
            self.harness.tick()
        self.assertNotEqual(json.loads(target.read_text())['timelineRef'], original_ref)
        self.assertEqual(read_program_payload(target, cache)['timelines']['demo:A']['segments'][0]['text'], '更新歌词')

    def test_program_supports_two_large_validated_timelines(self):
        self.harness.bridge.queue(replay())
        self.harness.read_bridge()
        for deck in ('A', 'B'):
            key = 'demo:' + deck
            self.harness.timelines[key] = dict(timeline(), versionId=key, segments=[
                {'startMs': index * 2, 'endMs': index * 2 + 2, 'text': '长' * 500}
                for index in range(1600)
            ])
        with patch('app.controller.monotonic_ns', return_value=NOW):
            self.harness.tick()
        path = self.runtime / 'program.json'
        self.assertLess(path.stat().st_size, 100_000)
        ref = json.loads(path.read_bytes())['timelineRef']
        self.assertGreater((self.runtime / f'program-timelines-{ref["slot"]}.json').stat().st_size, 4_000_000)
        self.assertEqual(read_program_payload(path)['timelines'], self.harness.timelines)


class IndependentRendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'program.json'
        self.harness = SimpleNamespace(path=self.path, clock=NativeClock(Path(__file__).resolve().parents[1]),
                                       _state=initial_state(), payload=None, last_mtime=None,
                                       stateChanged=FakeSignal())
        state = initial_state()
        state['activeDeck'] = 'A'
        self.payload = {'state': state, 'raw': replay()['decks'], 'timelines': {'demo:A': timeline()}}
        self.path.write_text(json.dumps(self.payload))

    def tearDown(self):
        self.temp.cleanup()

    def tick(self, now):
        with patch('app.controller.monotonic_ns', return_value=now):
            FileController.tick(self.harness)

    def test_renderer_reprojects_unchanged_file_and_expires_without_console(self):
        self.tick(NOW)
        self.assertTrue(self.harness._state['lyric']['visible'])
        old_mtime = self.harness.last_mtime
        self.tick(NOW + 200_000_000)
        self.assertEqual(self.harness._state['deckA']['positionMs'], 1450)
        self.assertEqual(self.harness.last_mtime, old_mtime)
        self.tick(NOW + 350_000_001)
        self.assertFalse(self.harness._state['lyric']['visible'])
        self.assertTrue(self.harness._state['stale'])

    def test_stopped_state_overrides_residual_raw_in_renderer(self):
        self.payload['state']['bridgeRunning'] = False
        self.path.write_text(json.dumps(self.payload))
        self.tick(NOW)
        self.assertFalse(self.harness._state['lyric']['visible'])
        self.assertEqual(self.harness._state['effectiveSource'], 'none')

    def test_program_stop_gate_must_be_boolean(self):
        for malformed in (0, 1, 'false', None):
            payload = copy.deepcopy(self.payload)
            payload['state']['bridgeRunning'] = malformed
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                validate_program_payload(payload)

    def test_malformed_program_file_immediately_hides_prior_words(self):
        self.tick(NOW)
        self.assertTrue(self.harness._state['lyric']['visible'])
        self.path.write_text('{invalid JSON')
        self.tick(NOW + 10_000_000)
        self.assertFalse(self.harness._state['lyric']['visible'])
        self.assertTrue(self.harness._state['stale'])

    def test_temporary_screen_content_expires_while_snapshot_is_corrupt(self):
        state = self.payload['state']
        state['screenLayers']['text'].update(text='限时文字', visible=True)
        state['screenTextValidUntilNs'] = NOW + 100_000_000
        state['guestMessage'] = {'text': '限时祝福', 'visible': True}
        state['guestMessageValidUntilNs'] = NOW + 100_000_000
        self.path.write_text(json.dumps(self.payload))
        self.tick(NOW)
        self.assertTrue(self.harness._state['screenLayers']['text']['visible'])
        self.assertTrue(self.harness._state['guestMessage']['visible'])
        self.path.write_text('{invalid JSON')
        self.tick(NOW + 10_000_000)
        self.assertFalse(self.harness._state['lyric']['visible'])
        self.tick(NOW + 100_000_000)
        self.assertFalse(self.harness._state['screenLayers']['text']['visible'])
        self.assertFalse(self.harness._state['guestMessage']['visible'])


class FileProofRegressionTests(unittest.TestCase):
    def test_in_place_same_size_mutation_with_preserved_mtime_invalidates_old_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.wav'
            with wave.open(str(source), 'wb') as stream:
                stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0\0' * 24000)
            proof = inspect_audio(source)
            before = source.stat()
            with source.open('r+b') as stream:
                stream.seek(44)
                stream.write(b'\x01\x00')
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            after = source.stat()
            self.assertEqual((before.st_size, before.st_mtime_ns, before.st_ino),
                             (after.st_size, after.st_mtime_ns, after.st_ino))
            self.assertNotEqual(inspect_audio(source)['versionId'], proof['versionId'])
            self.assertFalse(proof_is_current(proof, str(source)))

    def test_renderer_proof_rechecks_file_even_if_console_snapshot_is_unchanged(self):
        from app.controller import project_state
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.wav'
            with wave.open(str(source), 'wb') as stream:
                stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0\0' * 24000)
            proof = inspect_audio(source)
            sample = live_frame()
            sample['decks'][0]['audio_path'] = str(source)
            sample['fader']['normalized'] = 0
            raw = adapt_serato_frame(sample, {str(source): proof})
            state = initial_state()
            state.update(mode='serato', selectedSource='A', videoFader=0,
                         faderValidUntilNs=sample['validUntilNs'])
            lyrics = timeline()
            lyrics.update(versionId=proof['versionId'], durationMs=3000)
            clock = NativeClock(Path(__file__).resolve().parents[1])
            before = project_state(state, raw, {proof['versionId']: lyrics}, clock, NOW)
            self.assertTrue(before['lyric']['visible'])
            source.unlink()
            after = project_state(state, raw, {proof['versionId']: lyrics}, clock, NOW)
            self.assertFalse(after['lyric']['visible'])
            self.assertEqual(after['selection']['reasonCode'], 'identity_unverified')


if __name__ == '__main__':
    unittest.main()
