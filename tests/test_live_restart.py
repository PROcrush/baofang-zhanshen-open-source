"""Restart restores operator intent while discarding every playback observation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from app.controller import Controller, initial_state
from app.live_settings import load_live_session, save_live_session


class LiveRestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        self.root = Path(__file__).resolve().parents[1]
        self.started = []

        def start(controller, mode):
            payload = json.loads((self.runtime / 'program.json').read_text())
            self.assertFalse(payload['state']['lyric']['visible'])
            self.assertEqual(payload['raw'], {})
            self.started.append(mode)

        p = patch.object(Controller, 'start_bridge', start)
        p.start()
        self.addCleanup(p.stop)

    def controller(self, **kwargs):
        c = Controller(self.root, self.runtime, **kwargs)
        c.timer.stop()
        self.addCleanup(lambda: c.shutdown() if not c.shutting_down else None)
        return c

    def test_restart_keeps_hidden_b_and_existing_preferences_without_old_playback(self):
        first = self.controller()
        first.selectSource('B')
        first.clearOutput()
        first.setInputMode('serato')
        first._state['lyricOffsets'] = {'sha256:test': 250}
        first._state['outputTarget'] = {'screenName': 'TV', 'fullScreen': True}
        first.setStyle('liveEntrance', 'ripple')
        first._state['deckA'].update(positionMs=52000, valid=True)
        first._state.update(readerConnected=True, bridgeEpoch='old', videoFader=1,
                            guestMessage={'visible': True, 'text': '旧插字'})
        # State is already durable before shutdown; there is no exit-only save.
        first.shutdown()
        second = self.controller()
        for key in ('mode', 'inputMode', 'selectedSource', 'resumeSource', 'lyricOffsets',
                    'outputTarget', 'style'):
            self.assertEqual(second.state[key], first.state[key], key)
        self.assertEqual(self.started, ['replay', 'serato', 'serato'])
        self.assertEqual(second.raw, {})
        self.assertEqual(second.file_proofs, {})
        self.assertEqual(second.ax_bindings, {})
        self.assertFalse(second.state['readerConnected'])
        self.assertFalse(second.state['outputOpen'])
        self.assertFalse(second.state['outputRequested'])
        self.assertFalse(second.state['guestMessage']['visible'])
        self.assertFalse(second.state['lyric']['visible'])
        second.restoreOutput()
        self.assertEqual(second.state['selectedSource'], 'B')
        self.assertFalse(second.state['lyric']['visible'])

    def test_explicit_startup_mode_wins_without_starting_replay_or_unhiding(self):
        state = initial_state()
        state.update(inputMode='serato', selectedSource='none', resumeSource='auto')
        save_live_session(self.runtime, state)
        c = self.controller(input_mode='rekordbox_ax')
        self.assertEqual(self.started, ['rekordbox_ax'])
        self.assertEqual(c.state['selectedSource'], 'none')
        self.assertEqual(c.state['resumeSource'], 'A')
        self.assertEqual(load_live_session(self.runtime)['inputMode'], 'rekordbox_ax')
        self.assertEqual(c.ax_bindings, {})
        self.assertFalse(c.state['lyric']['visible'])

    def test_restart_rekordbox_cannot_restore_serato_overlay_destination(self):
        c = self.controller(input_mode='rekordbox')
        c._state['outputDestination'] = 'serato_overlay'
        c.save_settings()
        c.shutdown()
        restarted = self.controller()
        self.assertEqual(restarted.state['outputDestination'], 'standalone')
        self.assertEqual(self.started, ['rekordbox', 'rekordbox'])

    def test_disk_failure_keeps_emergency_hide_and_reports_retry(self):
        c = self.controller()
        c.selectSource('B')
        before = (self.runtime / 'live-session.json').read_bytes()
        with patch('app.controller.save_live_session', side_effect=OSError('full')):
            c.clearOutput()
        self.assertEqual(c.state['selectedSource'], 'none')
        self.assertEqual(c.state['resumeSource'], 'B')
        self.assertIn('现场状态未保存', c.state['notice'])
        self.assertEqual((self.runtime / 'live-session.json').read_bytes(), before)
        c.clearOutput()
        self.assertNotIn('未保存', c.state['notice'])
        self.assertEqual(load_live_session(self.runtime)['selectedSource'], 'none')

    def test_broken_partial_or_unknown_session_uses_defaults(self):
        default = load_live_session(self.runtime)
        path = self.runtime / 'live-session.json'
        for value in ('{', '[]', 'null', '{"schemaVersion":1}',
                      json.dumps({'schemaVersion': 2, 'inputMode': 'serato',
                                  'selectedSource': 'none', 'resumeSource': 'B'}),
                      json.dumps({'schemaVersion': 1, 'inputMode': 'other',
                                  'selectedSource': 'none', 'resumeSource': 'B'}),
                      'x' * 4097):
            with self.subTest(value=value[:80]):
                path.write_text(value)
                self.assertEqual(load_live_session(self.runtime), default)
        path.write_text('[]')
        (self.runtime / 'settings.json').write_text('[]')
        c = self.controller()
        self.assertEqual(c.state['mode'], 'replay')

    def test_failed_atomic_replace_preserves_prior_session(self):
        state = initial_state()
        save_live_session(self.runtime, state)
        path = self.runtime / 'live-session.json'
        before = path.read_bytes()
        state.update(selectedSource='none', resumeSource='B')
        with patch('app.lyric_cache.os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                save_live_session(self.runtime, state)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.runtime.glob('.lyric-*')), [])

    def test_session_contains_only_choices_and_normalizes_manual_ax(self):
        state = initial_state()
        state.update(inputMode='rekordbox_ax', selectedSource='none', resumeSource='auto')
        save_live_session(self.runtime, state)
        saved = json.loads((self.runtime / 'live-session.json').read_text())
        self.assertEqual(set(saved), {'schemaVersion', 'inputMode', 'selectedSource', 'resumeSource'})
        self.assertEqual(load_live_session(self.runtime),
                         {'inputMode': 'rekordbox_ax', 'selectedSource': 'none', 'resumeSource': 'A'})


if __name__ == '__main__':
    unittest.main()
