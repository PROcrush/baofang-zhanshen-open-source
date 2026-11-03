"""Controller and independent-renderer acceptance for manual original-rekordbox AX."""
import copy
import json
from pathlib import Path
import tempfile
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from PySide6.QtCore import QCoreApplication

from app.controller import Controller, FileController, initial_state
from app.messages import validate_program_payload
from app.native import NativeClock
from app.storage import Library, inspect_audio
from test_controller_modes import FakeBridge, FakeSignal
from test_rekordbox_ax_adapter import NOW, frame


class RekordboxAXControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name).resolve()
        self.root = Path(__file__).resolve().parents[1]
        self.library = Library(self.runtime)
        self.addCleanup(self.library.close)
        self.proofs, self.timelines = {}, {}
        for i, side in enumerate('AB'):
            source = self.runtime / f'actual-{side}.wav'
            with wave.open(str(source), 'wb') as stream:
                stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                stream.writeframes(bytes([i, 0]) * 8000 * (3 + i))
            proof = inspect_audio(source)
            self.proofs[side] = proof
            self.library.add_track(proof)
            timeline = {'schemaVersion': 1, 'versionId': proof['versionId'],
                        'durationMs': proof['durationMs'], 'quality': 'sentence',
                        'segments': [{'startMs': 1000, 'endMs': 2000, 'text': '歌曲' + side}]}
            self.library.add_timeline(timeline, verified_audio=proof)
            self.timelines[proof['versionId']] = timeline
        self.sample = frame()
        self.h = SimpleNamespace(
            _state=initial_state(), raw={}, timelines=self.timelines,
            library=self.library, bridge=FakeBridge(), stateChanged=FakeSignal(),
            runtime=self.runtime, root=self.root, clock=NativeClock(self.root), output=None,
            file_proofs={}, file_errors={}, index_pending={}, failed_file_signatures={},
            ax_frame=None, ax_bindings={}, ax_binding_notes={},
            _offline_rows=self.library.offline_tracks(), notices=[], indexed=[])
        self.h.notice = lambda value: (self.h.notices.append(value), self.h._state.update(notice=value))
        for name in ('send', 'read_bridge', 'stopBridge', 'start_bridge', 'tick', 'setInputMode',
                     'connectRekordboxAX', 'refresh_preparation', 'refresh_ax_view', 'read_ax_message',
                     'bindRekordboxAX', 'clearRekordboxAXBinding', 'selectSource'):
            setattr(self.h, name, MethodType(getattr(Controller, name), self.h))
        self.h.index_audio = self.immediate_index
        self.monotonic_patch = patch('app.controller.monotonic_ns', return_value=NOW)
        self.monotonic_patch.start()
        self.addCleanup(self.monotonic_patch.stop)
        self.h.connectRekordboxAX()

    def immediate_index(self, path, completed=None, failed=None):
        self.h.indexed.append(path)
        try:
            proof = inspect_audio(path)
            self.h.file_proofs[proof['path']] = proof
            if completed:
                completed(proof)
        except (OSError, ValueError) as exc:
            if failed:
                failed(str(exc))

    def deliver(self, sample=None):
        self.h.bridge.queue(self.sample if sample is None else sample)
        self.h.read_bridge()
        self.h.tick()

    def bind(self, side):
        self.h.bindRekordboxAX(side, self.proofs[side]['versionId'])

    def defer_index(self):
        pending = []
        self.h.index_audio = lambda path, completed, failed: pending.append((path, completed, failed))
        return pending

    def renderer(self):
        return SimpleNamespace(path=self.runtime / 'program.json', clock=self.h.clock,
                               _state=initial_state(), payload=None, last_mtime=None,
                               stateChanged=FakeSignal())

    def test_ax_mode_replaces_auto_with_manual_a_and_rejects_auto_requests(self):
        self.assertEqual(self.h._state['mode'], 'rekordbox_ax')
        self.assertEqual(self.h.bridge.arguments, ['-m', 'probes.rekordbox_ax', '--application-host'])
        self.assertEqual(self.h._state['selectedSource'], 'A')
        self.assertEqual(self.h._state['resumeSource'], 'A')
        self.h.selectSource('B')
        self.h.selectSource('auto')
        self.assertEqual(self.h._state['selectedSource'], 'B')
        self.assertIn('手动', self.h._state['notice'])
        self.assertEqual(self.h.bridge.writes, [])

    def test_unbound_observations_show_each_position_without_file_guessing_or_lyrics(self):
        self.deliver()
        self.assertTrue(self.h._state['readerConnected'])
        self.assertEqual(self.h._state['deckA']['title'], '同名歌曲 Remix')
        self.assertEqual(self.h._state['deckA']['positionMs'], 1200)
        self.assertEqual(self.h._state['deckB']['positionMs'], 1700)
        self.assertFalse(self.h._state['lyric']['visible'])
        self.assertEqual(self.h.indexed, [])
        self.assertEqual(self.h.ax_bindings, {})
        view = self.h._state['rekordboxAX']
        self.assertTrue(view['observed']['A']['available'])
        self.assertEqual(len(view['candidates']), 2)
        self.assertEqual(self.h.raw['A']['path'], '')

    def test_confirming_exact_files_enables_manual_ab_and_publishes_renderer_contract(self):
        self.deliver()
        self.bind('A')
        self.bind('B')
        self.assertEqual(self.h._state['lyric']['text'], '歌曲A')
        self.h.selectSource('B')
        self.assertEqual(self.h._state['lyric']['text'], '歌曲B')
        self.assertEqual(self.h._state['effectiveSource'], 'B')
        self.assertEqual(set(self.h.ax_bindings), {'A', 'B'})
        self.assertTrue(self.h.raw['B']['identityReady'])
        self.assertEqual(self.h.raw['B']['versionId'], self.proofs['B']['versionId'])
        payload = json.loads((self.runtime / 'program.json').read_text())
        self.assertEqual(validate_program_payload(payload), payload)
        self.assertEqual(payload['state']['mode'], 'rekordbox_ax')
        self.assertNotIn('rekordboxAX', payload['state'])

    def test_reader_error_and_stop_clear_guest_lyrics_immediately(self):
        self.deliver()
        self.bind('A')
        guest = self.renderer()
        FileController.tick(guest)
        self.assertTrue(guest._state['lyric']['visible'])
        self.deliver({'type': 'rekordbox_ax_status', 'detail': 'AX 授权不可用'})
        self.assertFalse(self.h._state['readerConnected'])
        self.assertEqual(self.h.raw, {})
        self.assertFalse(self.h._state['lyric']['visible'])
        self.assertFalse(self.h._state['rekordboxAX']['observed']['A']['available'])
        FileController.tick(guest)
        self.assertFalse(guest._state['lyric']['visible'])
        self.sample['sequence'] += 1
        self.deliver()
        self.assertTrue(self.h._state['lyric']['visible'])
        self.h.stopBridge()
        self.h.tick()
        FileController.tick(guest)
        self.assertEqual(self.h.raw, {})
        self.assertFalse(guest._state['lyric']['visible'])

    def test_new_epoch_and_reconnect_require_new_per_deck_confirmation(self):
        self.deliver()
        self.bind('A')
        self.bind('B')
        self.sample.update(epoch='rekordbox-ax:second', sequence=1)
        self.deliver()
        self.assertEqual(self.h.ax_bindings, {})
        self.assertFalse(self.h._state['lyric']['visible'])
        self.assertIn('新会话', self.h._state['rekordboxAX']['bindings']['A']['status'])
        self.bind('A')
        self.assertTrue(self.h._state['lyric']['visible'])
        self.h.connectRekordboxAX()
        self.assertIsNone(self.h.ax_frame)
        self.assertEqual(self.h.ax_bindings, {})
        self.assertIsNone(self.h._state['bridgeEpoch'])
        self.assertEqual(self.h.raw, {})
        self.assertFalse(self.h._state['lyric']['visible'])

    def test_title_change_clears_only_the_changed_deck_binding(self):
        self.deliver()
        self.bind('A')
        self.bind('B')
        self.sample['decks'][0]['title'] = '不同标题的编辑版'
        self.sample['sequence'] += 1
        self.deliver()
        self.assertNotIn('A', self.h.ax_bindings)
        self.assertIn('B', self.h.ax_bindings)
        self.assertFalse(self.h._state['lyric']['visible'])
        self.h.selectSource('B')
        self.assertTrue(self.h._state['lyric']['visible'])

    def test_clear_binding_hides_only_selected_deck_and_requires_confirmation_again(self):
        self.deliver()
        self.bind('A')
        self.bind('B')
        self.h.clearRekordboxAXBinding('A')
        self.assertNotIn('A', self.h.ax_bindings)
        self.assertIn('B', self.h.ax_bindings)
        self.assertFalse(self.h._state['lyric']['visible'])
        self.assertEqual(self.h.raw['A']['path'], '')
        self.bind('A')
        self.assertTrue(self.h._state['lyric']['visible'])

    def test_expired_observation_rejects_confirmation_without_indexing(self):
        self.deliver()
        with patch('app.controller.monotonic_ns', return_value=NOW + 350_000_001):
            self.bind('A')
        self.assertEqual(self.h.indexed, [])
        self.assertEqual(self.h.ax_bindings, {})
        self.assertIn('新鲜', self.h._state['notice'])

    def test_async_file_verification_cannot_bind_after_deck_observation_changed(self):
        self.deliver()
        pending = self.defer_index()
        self.bind('A')
        self.assertEqual(len(pending), 1)
        self.assertFalse(self.h._state['lyric']['visible'])
        self.sample['sequence'] += 1
        self.sample['decks'][0].update(title='新加载歌曲', observationId='new-a')
        self.deliver()
        pending[0][1](self.proofs['A'])
        self.assertNotIn('A', self.h.ax_bindings)
        self.assertFalse(self.h._state['lyric']['visible'])
        self.assertIn('变化', self.h._state['notice'])

    def test_async_file_verification_rejects_expired_or_disconnected_reader(self):
        for state in ('expired', 'disconnected'):
            with self.subTest(state=state):
                self.sample['sequence'] += 1
                self.deliver()
                pending = self.defer_index()
                self.bind('A')
                if state == 'disconnected':
                    self.deliver({'type': 'rekordbox_ax_status', 'detail': '原版已关闭'})
                    pending[0][1](self.proofs['A'])
                else:
                    with patch('app.controller.monotonic_ns', return_value=NOW + 350_000_001):
                        pending[0][1](self.proofs['A'])
                self.assertNotIn('A', self.h.ax_bindings)
                self.assertFalse(self.h._state['lyric']['visible'])

    def test_async_title_change_with_same_observation_id_must_not_bind_old_confirmation(self):
        self.deliver()
        pending = self.defer_index()
        self.bind('A')
        self.sample['sequence'] += 1
        self.sample['decks'][0]['title'] = '另一个同长度曲目'
        self.deliver()
        pending[0][1](self.proofs['A'])
        self.assertNotIn('A', self.h.ax_bindings)
        self.assertFalse(self.h._state['lyric']['visible'])

    def test_clearing_binding_cancels_pending_file_confirmation(self):
        self.deliver()
        pending = self.defer_index()
        self.bind('A')
        self.h.clearRekordboxAXBinding('A')
        pending[0][1](self.proofs['A'])
        self.assertNotIn('A', self.h.ax_bindings)
        self.assertFalse(self.h._state['lyric']['visible'])

    def test_renderer_uses_ax_observations_without_extrapolation_and_expires_independently(self):
        self.deliver()
        self.bind('A')
        guest = self.renderer()
        FileController.tick(guest)
        self.assertEqual(guest._state['lyric']['text'], '歌曲A')
        first = copy.deepcopy(guest._state['lyric'])
        with patch('app.controller.monotonic_ns', return_value=NOW + 300_000_000):
            FileController.tick(guest)
        self.assertEqual(guest._state['deckA']['positionMs'], 1200)
        self.assertEqual(guest._state['lyric'], first)
        with patch('app.controller.monotonic_ns', return_value=NOW + 350_000_001):
            FileController.tick(guest)
        self.assertFalse(guest._state['lyric']['visible'])
        self.assertTrue(guest._state['stale'])
        payload = json.loads((self.runtime / 'program.json').read_text())
        payload['raw']['A'].pop('clockMode')
        with self.assertRaises(ValueError):
            validate_program_payload(payload)

    def test_real_controller_hashes_selected_file_in_worker_before_binding(self):
        # Exercise the real QObject/QProcess queue rather than replacing the
        # asynchronous index result with a test callback. No DJ process starts.
        separate_runtime = self.runtime / 'real-qt'
        separate_runtime.mkdir()
        library = Library(separate_runtime)
        for side in 'AB':
            library.add_track(self.proofs[side])
            library.add_timeline(self.timelines[self.proofs[side]['versionId']],
                                 verified_audio=self.proofs[side])
        library.close()
        started = []
        def fake_start(controller, mode):
            started.append(mode)
            controller._state['bridgeRunning'] = True
        with patch.object(Controller, 'start_bridge', fake_start), \
                patch.object(Controller, 'refreshOutputScreens'):
            controller = Controller(self.root, separate_runtime)
            self.addCleanup(controller.shutdown)
            controller.timer.stop()
            controller.connectRekordboxAX()
            controller.read_ax_message(copy.deepcopy(self.sample))
            self.assertNotIn(self.proofs['A']['path'], controller.file_proofs)
            controller.bindRekordboxAX('A', self.proofs['A']['versionId'])
            self.assertTrue(controller.index_pending)
            self.assertFalse(controller._state['lyric']['visible'])
            deadline = time.monotonic() + 5
            while controller.index_pending and time.monotonic() < deadline:
                self.application.processEvents()
                for process in tuple(controller.jobs):
                    process.waitForFinished(20)
            self.application.processEvents()
            self.assertEqual(controller.index_pending, {})
            self.assertEqual(controller.file_proofs[self.proofs['A']['path']]['versionId'],
                             self.proofs['A']['versionId'])
            self.assertEqual(controller.ax_bindings['A']['versionId'], self.proofs['A']['versionId'])
            self.assertEqual(controller._state['lyric']['text'], '歌曲A')
            self.assertEqual(started, ['replay', 'rekordbox_ax'])


if __name__ == '__main__':
    unittest.main()
