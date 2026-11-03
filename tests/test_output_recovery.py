"""Bounded renderer recovery preserves explicit closure and current live settings."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from app.controller import Controller, initial_state


class OutputRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.h = SimpleNamespace(
            _state=initial_state(), root=Path.cwd(), runtime=Path(self.temp.name),
            output=None, shutting_down=False, _output_retries=0,
            _output_retry_at=0, _output_started_at=0,
            stateChanged=SimpleNamespace(emit=Mock()), tick=Mock(), save_settings=Mock())
        self.h.notice = lambda value: self.h._state.update(notice=value)
        for name in ('openOutput', 'closeOutput', '_start_output', '_queue_output_retry',
                     'refresh_output', 'open_destination'):
            setattr(self.h, name, MethodType(getattr(Controller, name), self.h))
        self.now = 1_000_000_000
        clock = patch('app.controller.monotonic_ns', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        launch = patch('app.controller.subprocess.Popen', side_effect=lambda *a, **kw: Mock(poll=Mock(return_value=None)))
        self.launch = launch.start()
        self.addCleanup(launch.stop)

    def crash(self, code=-9):
        self.h.output.poll.return_value = code
        self.h.refresh_output()

    def retry(self, seconds):
        self.now += seconds * 1_000_000_000
        self.h.refresh_output()

    def test_crash_waits_then_restarts_with_current_hidden_source_and_target(self):
        self.h._state.update(selectedSource='none', resumeSource='B',
                             outputTarget={'screenName': 'TV', 'fullScreen': True},
                             lyricOffsets={'demo:B': 250})
        settings = copy.deepcopy(self.h._state)
        self.h.openOutput()
        first = self.h.output
        self.crash()
        self.assertFalse(self.h._state['outputOpen'])
        self.assertTrue(self.h._state['outputRequested'])
        self.assertEqual(self.h._state['outputRecovery']['phase'], 'waiting')
        self.retry(.9)
        self.assertEqual(self.launch.call_count, 1)
        self.retry(.1)
        self.assertIsNot(self.h.output, first)
        self.assertEqual(self.launch.call_count, 2)
        self.assertTrue(self.h._state['outputOpen'])
        for key in ('selectedSource', 'resumeSource', 'outputTarget', 'lyricOffsets', 'style'):
            self.assertEqual(self.h._state[key], settings[key])

    def test_repeated_crashes_stop_after_three_backoff_retries(self):
        self.h.openOutput()
        for index, delay in enumerate((1, 2, 4), 1):
            self.crash(2)
            self.retry(delay - .1)
            self.assertEqual(self.launch.call_count, index)
            self.retry(.1)
            self.assertEqual(self.launch.call_count, index + 1)
        self.crash(2)
        self.assertEqual(self.h._state['outputRecovery']['phase'], 'failed')
        self.assertFalse(self.h._state['outputRequested'])
        self.retry(120)
        self.assertEqual(self.launch.call_count, 4)
        self.h.openOutput()
        self.assertEqual(self.launch.call_count, 5)
        self.assertEqual(self.h._output_retries, 0)

    def test_launch_and_log_open_failures_are_bounded(self):
        for failure in ('spawn', 'log'):
            with self.subTest(failure=failure):
                self.h.closeOutput()
                self.launch.side_effect = OSError('启动失败') if failure == 'spawn' else lambda *a, **kw: Mock()
                if failure == 'log':
                    self.h.runtime = Path(self.temp.name) / 'missing'
                self.h.openOutput()
                for delay in (1, 2, 4):
                    self.retry(delay)
                self.assertEqual(self.h._state['outputRecovery']['phase'], 'failed')
                self.assertIsNone(self.h.output)
                self.assertFalse(self.h._state['outputOpen'])

    def test_active_and_pending_open_requests_do_not_duplicate_processes(self):
        self.h.openOutput()
        self.h.openOutput()
        self.assertEqual(self.launch.call_count, 1)
        self.crash()
        self.h.openOutput()
        self.assertEqual(self.launch.call_count, 1)

    def test_close_cancels_pending_recovery(self):
        self.h.openOutput()
        self.crash()
        self.h.closeOutput()
        self.retry(10)
        self.assertEqual(self.launch.call_count, 1)
        self.assertFalse(self.h._state['outputRequested'])
        self.assertEqual(self.h._state['outputRecovery']['phase'], 'idle')

    def test_normal_window_close_does_not_restart(self):
        self.h.openOutput()
        self.crash(0)
        self.retry(10)
        self.assertFalse(self.h._state['outputRequested'])
        self.assertEqual(self.launch.call_count, 1)

    def test_controller_close_terminates_without_recovery(self):
        self.h.openOutput()
        process = self.h.output
        self.h.closeOutput()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=1)
        self.retry(10)
        self.assertEqual(self.launch.call_count, 1)

    def test_stable_minute_replenishes_retry_budget(self):
        self.h.openOutput()
        self.crash()
        self.retry(1)
        self.retry(59)
        self.assertEqual(self.h._output_retries, 1)
        self.retry(1)
        self.assertEqual(self.h._output_retries, 0)
        self.crash()
        self.assertIn('1/3', self.h._state['outputRecovery']['detail'])

    def test_route_change_cancels_old_recovery_and_launches_correct_renderer(self):
        self.h.openOutput()
        self.crash()
        self.h.open_destination('serato_overlay')
        self.assertIn('--serato-overlay', self.launch.call_args.args[0])
        self.assertEqual(self.h._output_retry_at, 0)
        self.retry(10)
        self.assertEqual(self.launch.call_count, 2)
        self.crash()
        self.h.open_destination('standalone')
        self.assertNotIn('--serato-overlay', self.launch.call_args.args[0])
        self.assertEqual(self.launch.call_count, 3)

    def test_shutdown_never_launches_or_restarts_output(self):
        self.h.openOutput()
        self.crash()
        self.h.shutting_down = True
        self.retry(10)
        self.h.openOutput()
        self.assertEqual(self.launch.call_count, 1)


if __name__ == '__main__':
    unittest.main()
