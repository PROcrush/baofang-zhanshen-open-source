"""Preparation orchestration with real files/SQLite and entirely fake cloud IO."""
import copy
import json
import os
import socket
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QProcess, QProcessEnvironment

from app.preparation import PreparationManager
from app.storage import Library, inspect_audio


FAKE_KEY = 'TEST_ONLY_FAKE_KEY_NEVER_SENT_TO_A_SERVICE'


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, callback):
        self.slots.append(callback)

    def emit(self, *args):
        for callback in list(self.slots):
            callback(*args)


class FakeTimer:
    def __init__(self, _parent):
        self.timeout = FakeSignal()
        self.stopped = False

    def start(self, interval):
        self.interval = interval

    def stop(self):
        self.stopped = True


class FakeProcess:
    """Records the boundary without starting a subprocess or opening a socket."""
    def __init__(self, _parent):
        self.readyReadStandardOutput = FakeSignal()
        self.finished = FakeSignal()
        self.errorOccurred = FakeSignal()
        self.stdout = b''
        self.current_state = QProcess.ProcessState.NotRunning
        self.deleted = False
        self.killed = False
        self.on_kill = None

    def setWorkingDirectory(self, directory):
        self.directory = directory

    def setProcessEnvironment(self, environment):
        self.environment = environment

    def start(self, executable, arguments):
        self.executable, self.arguments = executable, arguments
        self.current_state = QProcess.ProcessState.Running

    @property
    def action(self):
        return self.arguments[self.arguments.index('--action') + 1]

    @property
    def request(self):
        path = self.arguments[self.arguments.index('--plan') + 1]
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def queue(self, payload, *, notify=True):
        self.stdout += (json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8')
        if notify:
            self.readyReadStandardOutput.emit()

    def readAllStandardOutput(self):
        result, self.stdout = self.stdout, b''
        return result

    def finish(self, code=0):
        self.current_state = QProcess.ProcessState.NotRunning
        self.finished.emit(code, QProcess.ExitStatus.NormalExit)

    def state(self):
        return self.current_state

    def deleteLater(self):
        self.deleted = True

    def kill(self):
        self.killed = True
        self.current_state = QProcess.ProcessState.NotRunning
        if self.on_kill:
            self.on_kill()

    def waitForFinished(self, timeout):
        self.wait_timeout = timeout
        return True


class PreparationManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.runtime = self.root / 'runtime'
        self.library = Library(self.root / 'library')
        self.timelines, self.processes, self.changes, self.published = {}, [], [], []
        self.managers = []
        self.network_attempts = []

        def no_network(*args, **kwargs):
            self.network_attempts.append((args, kwargs))
            raise AssertionError('Preparation tests must never use a network connection')

        environment = QProcessEnvironment()
        environment.insert('BAOFANG_302_API_KEY', 'TEST_INHERITED_KEY')
        self.patchers = [patch('app.preparation.QTimer', FakeTimer),
                         patch('app.preparation.QProcessEnvironment.systemEnvironment',
                               side_effect=lambda: QProcessEnvironment(environment)),
                         patch('socket.create_connection', no_network),
                         patch.object(socket.socket, 'connect', no_network),
                         patch.object(socket.socket, 'connect_ex', no_network)]
        for patcher in self.patchers:
            patcher.start()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.library.close)
        self.addCleanup(self.cleanup_managers)
        for patcher in self.patchers:
            self.addCleanup(patcher.stop)
        self.manager = self.new_manager()
        self.track = self.audio('当前曲目.wav')

    def tearDown(self):
        self.assertEqual(self.network_attempts, [], 'A test attempted real networking')

    def cleanup_managers(self):
        for manager in self.managers:
            manager.shutdown()

    def factory(self, parent):
        result = FakeProcess(parent)
        self.processes.append(result)
        return result

    def new_manager(self):
        result = PreparationManager(self.root, self.runtime, self.library, self.timelines,
                                    lambda: self.changes.append(True),
                                    lambda version, value: self.published.append((version, copy.deepcopy(value))),
                                    process_factory=self.factory)
        self.managers.append(result)
        return result

    def audio(self, name, sample=0):
        path = self.root / name
        with wave.open(str(path), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(int(sample).to_bytes(2, 'little', signed=True) * 24000)
        track = inspect_audio(path)
        track['identityVerified'] = True
        return track

    def configure(self, manager=None, budget=1):
        path = self.root / 'test-config.json'
        path.write_text(json.dumps({'apiKey': FAKE_KEY, 'budgetLimitPTC': budget,
                                    'route': 'domestic'}), encoding='utf-8')
        (manager or self.manager).configure_file(str(path))

    def payload(self, track=None, text='往事随疯'):
        track = track or self.track
        return {'schemaVersion': 1, 'versionId': track['versionId'],
                'durationMs': track['durationMs'], 'quality': 'word',
                'timingProvenance': 'provider_token', 'acousticAccuracyVerified': False,
                'segments': [{'startMs': 1000, 'endMs': 2000, 'text': text,
                              'words': [{'startMs': 1000, 'endMs': 1400, 'text': text[:2]},
                                        {'startMs': 1600, 'endMs': 2000, 'text': text[2:]}]}]}

    def cache(self, track=None, payload=None):
        track = track or self.track
        payload = payload or self.payload(track)
        self.library.add_track(track)
        self.library.add_timeline(payload, track)
        self.timelines[track['versionId']] = copy.deepcopy(payload)
        return payload

    def prepare(self, track=None, *, cost=.1, manager=None):
        track = track or self.track
        manager = manager or self.manager
        manager.plan('A', track)
        process = self.processes[-1]
        self.complete_preflight(process, track, cost)
        return manager.plans[track['versionId']]

    def complete_preflight(self, process, track=None, cost=.1):
        track = track or self.track
        request = process.request
        process.queue({'phase': 'planned', 'plan': {
            'versionId': track['versionId'], 'sourcePath': track['path'],
            'durationMs': track['durationMs'], 'estimatedCostPTC': cost,
            'host': request['host'], 'service': 'test-only-fixture',
        }})
        process.finish()

    def start(self, track=None, cost=.1):
        track = track or self.track
        self.configure()
        self.manager.set_new_submission_enabled(True)
        plan = self.prepare(track, cost=cost)
        self.manager.start('A', track, plan['planId'])
        return self.processes[-1]

    def result_file(self, payload=None, *, inside=True):
        payload = payload or self.payload()
        directory = self.manager._receipt(self.track['versionId']).parent if inside else self.root
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'timeline.json'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return path

    def receipt(self, *, state='polling', task_id='fake-provider-task'):
        path = self.manager._receipt(self.track['versionId'])
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'versionId': self.track['versionId'], 'state': state, 'taskId': task_id,
                   'host': 'api.302ai.cn', 'durationMs': self.track['durationMs'],
                   'reservedCostPTC': .1,
                   'plan': {'versionId': self.track['versionId'], 'sourcePath': self.track['path'],
                            'durationMs': self.track['durationMs'], 'host': 'api.302ai.cn'}}
        path.write_text(json.dumps(payload), encoding='utf-8')
        return payload

    def test_new_paid_submission_is_disabled_even_after_config_and_plan(self):
        self.configure()
        plan = self.prepare()
        count = len(self.processes)
        view = self.manager.view({'A': self.track})
        self.assertFalse(view['config']['newSubmissionEnabled'])
        self.assertFalse(view['decks']['A']['plan']['canSubmit'])
        with self.assertRaisesRegex(ValueError, '尚未允许'):
            self.manager.start('A', self.track, plan['planId'])
        self.assertEqual(len(self.processes), count)
        self.assertEqual(self.manager.reservations, {})

    def test_preflight_has_no_key_and_exposes_concrete_file_cost_route(self):
        self.configure()
        plan = self.prepare(cost=.125)
        process = self.processes[-1]
        self.assertEqual(process.action, 'preflight')
        self.assertEqual(process.environment.value('BAOFANG_TASK_KEY'), '')
        self.assertFalse(process.environment.contains('BAOFANG_302_API_KEY'))
        self.assertEqual(plan['sourcePath'], self.track['path'])
        self.assertEqual(plan['versionId'], self.track['versionId'])
        self.assertEqual(plan['durationMs'], 3000)
        self.assertEqual(plan['estimatedCostPTC'], .125)
        self.assertEqual(plan['routeLabel'], '国内路线')
        self.assertTrue(plan['planId'])
        self.assertTrue(process.deleted)

    def test_cached_version_reuses_library_without_paid_start(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        plan = self.prepare()
        original = self.cache()
        count = len(self.processes)
        with self.assertRaisesRegex(ValueError, '复用'):
            self.manager.start('A', self.track, plan['planId'])
        self.assertEqual(len(self.processes), count)
        self.assertEqual(self.library.timeline(self.track['versionId']), original)
        self.assertTrue(self.manager.view({'A': self.track})['decks']['A']['canExport'])

    def test_wrong_plan_id_and_changed_settings_invalidate_plan(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        plan = self.prepare()
        with self.assertRaisesRegex(ValueError, '计划已失效'):
            self.manager.start('A', self.track, 'a-plan-from-an-old-screen')
        self.manager.set_route('international')
        with self.assertRaisesRegex(ValueError, '计划已失效'):
            self.manager.start('A', self.track, plan['planId'])
        self.assertEqual(self.manager.reservations, {})
        self.assertEqual([process.action for process in self.processes], ['preflight'])

    def test_inflight_preflight_cannot_restore_a_plan_after_route_change(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        self.manager.plan('A', self.track)
        process = self.processes[-1]
        old_request = process.request
        self.manager.set_route('international')
        process.queue({'phase': 'planned', 'plan': {
            'versionId': self.track['versionId'], 'sourcePath': self.track['path'],
            'durationMs': 3000, 'estimatedCostPTC': .1,
            'host': old_request['host'],
        }})
        process.finish()
        self.assertNotIn(self.track['versionId'], self.manager.plans)
        self.assertEqual(self.manager.route, 'international')
        self.assertEqual(self.manager.reservations, {})

    def test_inflight_preflight_cannot_restore_a_plan_after_budget_change(self):
        self.configure()
        self.manager.plan('A', self.track)
        process = self.processes[-1]
        self.manager.set_budget(.5)
        self.complete_preflight(process)
        self.assertNotIn(self.track['versionId'], self.manager.plans)
        self.assertEqual(self.manager.budget, .5)
        self.assertEqual(self.manager.reservations, {})

    def test_inflight_preflight_cannot_restore_a_plan_after_configuration_change(self):
        self.configure()
        self.manager.plan('A', self.track)
        process = self.processes[-1]
        self.configure(budget=.5)
        self.complete_preflight(process)
        self.assertNotIn(self.track['versionId'], self.manager.plans)
        self.assertEqual(self.manager.budget, .5)
        self.assertEqual(self.manager.reservations, {})

    def test_malformed_preflight_cost_cannot_create_plan_or_budget_reservation(self):
        for cost in (-.1, float('nan'), float('inf'), True):
            with self.subTest(cost=cost):
                manager = self.new_manager()
                self.configure(manager)
                manager.set_new_submission_enabled(True)
                manager.plan('A', self.track)
                self.complete_preflight(self.processes[-1], cost=cost)
                self.assertNotIn(self.track['versionId'], manager.plans)
                self.assertEqual(manager.reservations, {})
                self.assertEqual(manager.reports[self.track['versionId']]['phase'], 'blocked')

    def test_source_mutation_preserving_size_and_mtime_rejects_old_plan(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        plan = self.prepare()
        path = Path(self.track['path'])
        original = path.stat()
        body = bytearray(path.read_bytes())
        body[-1] ^= 1
        path.write_bytes(body)
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(path.stat().st_size, self.track['size'])
        self.assertEqual(path.stat().st_mtime_ns, self.track['mtimeNs'])
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.manager.start('A', self.track, plan['planId'])
        self.assertEqual(self.manager.reservations, {})
        self.assertEqual([process.action for process in self.processes], ['preflight'])

    def test_current_deck_path_cannot_be_swapped_for_an_old_plan(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        plan = self.prepare()
        second = self.audio('另一个同内容副本.wav')
        self.assertEqual(second['versionId'], self.track['versionId'])
        with self.assertRaisesRegex(ValueError, '当前盘或源文件已变化'):
            self.manager.start('A', second, plan['planId'])
        self.assertEqual(self.manager.reservations, {})

    def test_budget_reservation_precedes_dispatch_and_survives_restart(self):
        process = self.start(cost=.35)
        version = self.track['versionId']
        reservation_path = self.manager._receipt(version).with_name('reservation.json')
        reservation = json.loads(reservation_path.read_text())
        self.assertEqual(reservation['reservedCostPTC'], .35)
        self.assertEqual(reservation['intentId'], process.request['intentId'])
        self.assertEqual(reservation_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.manager.view({'A': self.track})['config']['spent'], .35)
        self.manager.shutdown()
        restarted = self.new_manager()
        self.assertEqual(restarted.reservations, {version: .35})
        self.assertEqual(restarted.budget, 1)
        self.assertFalse(restarted.enabled)
        self.assertEqual(restarted._key, '')
        self.configure(restarted)
        restarted.set_new_submission_enabled(True)
        plan = self.prepare(manager=restarted, cost=.35)
        with self.assertRaisesRegex(ValueError, '预留记录'):
            restarted.start('A', self.track, plan['planId'])
        self.assertEqual([item.action for item in self.processes], ['preflight', 'start', 'preflight'])

    def test_aggregate_budget_blocks_second_file_before_new_dispatch(self):
        self.start(cost=.7)
        second = self.audio('第二首.wav', sample=1)
        plan = self.prepare(second, cost=.4)
        count = len(self.processes)
        with self.assertRaisesRegex(ValueError, '剩余额度不足'):
            self.manager.start('B', second, plan['planId'])
        self.assertEqual(len(self.processes), count)
        self.assertNotIn(second['versionId'], self.manager.reservations)

    def test_key_is_only_child_environment_never_public_or_input_files(self):
        process = self.start()
        self.assertEqual(process.environment.value('BAOFANG_TASK_KEY'), FAKE_KEY)
        self.assertEqual(process.environment.value('PYTHONDONTWRITEBYTECODE'), '1')
        self.assertEqual(process.environment.value('PYTHONNOUSERSITE'), '1')
        self.assertFalse(process.environment.contains('BAOFANG_302_API_KEY'))
        process.queue({'phase': 'polling', 'detail': 'fixture ' + FAKE_KEY + ' https://example.invalid/file'})
        view_text = json.dumps(self.manager.view({'A': self.track}), ensure_ascii=False)
        self.assertNotIn(FAKE_KEY, view_text)
        self.assertIn('[密钥]', view_text)
        self.assertNotIn('example.invalid', view_text)
        self.assertNotIn(FAKE_KEY, json.dumps(process.arguments))
        for path in self.runtime.rglob('*.json'):
            with self.subTest(path=path.name):
                self.assertNotIn(FAKE_KEY, path.read_text(encoding='utf-8'))

    def test_detail_truncation_cannot_expose_a_partial_key(self):
        process = self.start()
        process.queue({'phase': 'polling', 'detail': 'x' * 980 + FAKE_KEY + ' trailing detail'})
        view = json.dumps(self.manager.view({'A': self.track}), ensure_ascii=False)
        self.assertNotIn(FAKE_KEY[:20], view)
        self.assertIn('[密钥]', view)

    def test_config_rotation_still_redacts_key_of_already_running_child(self):
        process = self.start()
        second_key = 'TEST_ONLY_SECOND_FAKE_KEY_NEVER_SENT'
        path = self.root / 'second-test-config.json'
        path.write_text(json.dumps({'apiKey': second_key, 'budgetLimitPTC': 1}), encoding='utf-8')
        self.manager.configure_file(str(path))
        self.assertEqual(process.environment.value('BAOFANG_TASK_KEY'), FAKE_KEY)
        process.queue({'phase': 'polling', 'detail': 'old process fixture ' + FAKE_KEY})
        view = json.dumps(self.manager.view({'A': self.track}), ensure_ascii=False)
        self.assertNotIn(FAKE_KEY, view)
        self.assertNotIn(second_key, view)
        self.assertIn('[密钥]', view)

    def test_receipt_query_is_pinned_to_original_host_and_never_resubmits(self):
        self.configure()
        self.receipt()
        self.manager.set_route('international')
        self.manager.query('A', self.track)
        process = self.processes[-1]
        self.assertEqual(process.action, 'query')
        self.assertEqual(process.request['host'], 'api.302ai.cn')
        self.manager.query('A', self.track)
        self.assertEqual(len(self.processes), 1)
        process.queue({'phase': 'polling', 'taskId': 'fake-provider-task'})
        process.finish()
        self.manager.timer.timeout.emit()
        self.assertEqual([item.action for item in self.processes], ['query', 'query'])
        self.assertFalse(self.manager.enabled)

    def test_unknown_receipt_prevents_new_submission_and_auto_poll_guessing(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        plan = self.prepare()
        self.receipt(state='unknown', task_id='')
        self.manager.timer.timeout.emit()
        with self.assertRaisesRegex(ValueError, '任务回执'):
            self.manager.start('A', self.track, plan['planId'])
        self.assertEqual([item.action for item in self.processes], ['preflight'])
        self.assertFalse(self.manager.view({'A': self.track})['decks']['A']['canQuery'])

    def test_successful_ready_result_binds_library_and_notifies_once(self):
        process = self.start()
        payload = self.payload()
        path = self.result_file(payload)
        process.queue({'phase': 'ready', 'timelinePath': str(path)})
        process.finish()
        process.finished.emit(0)
        version = self.track['versionId']
        self.assertEqual(self.library.timeline(version), payload)
        self.assertEqual(self.timelines[version], payload)
        self.assertEqual(self.published, [(version, payload)])
        self.assertEqual(self.library.get_track(version)['quality'], 'word')
        self.assertEqual(self.manager.view({'A': self.track})['decks']['A']['phase'], 'ready')
        self.assertNotIn(version, self.manager.processes)

    def test_stdout_chunks_split_inside_utf8_character_publish_once_after_full_frame(self):
        process = self.start()
        path = self.result_file()
        frame = (json.dumps({'phase': 'ready', 'detail': '云识别完成', 'timelinePath': str(path)},
                            ensure_ascii=False) + '\n').encode('utf-8')
        boundary = frame.index('云'.encode('utf-8')) + 1
        process.stdout = frame[:boundary]
        process.readyReadStandardOutput.emit()
        self.assertEqual(self.published, [])
        self.assertFalse(self.manager.processes[self.track['versionId']]['error'])
        process.stdout = frame[boundary:]
        process.readyReadStandardOutput.emit()
        process.finish()
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.library.timeline(self.track['versionId']), self.payload())

    def test_malformed_stdout_cannot_publish_even_with_later_ready_frame(self):
        process = self.start()
        path = self.result_file()
        process.stdout = b'invalid protocol frame\n'
        process.readyReadStandardOutput.emit()
        process.queue({'phase': 'ready', 'timelinePath': str(path)})
        process.finish()
        self.assertEqual(self.timelines, {})
        self.assertEqual(self.published, [])
        self.assertTrue(path.exists())
        self.assertEqual(self.manager.reports[self.track['versionId']]['phase'], 'unknown')

    def test_malformed_phase_is_reported_as_unknown_without_breaking_public_view(self):
        process = self.start()
        process.queue({'phase': [], 'detail': 'malformed test-only protocol payload'})
        view = self.manager.view({'A': self.track})
        self.assertEqual(view['decks']['A']['phase'], 'unknown')
        self.assertFalse(view['decks']['A']['plan']['canSubmit'])

    def test_result_after_source_replacement_is_retained_without_binding(self):
        process = self.start()
        path = self.result_file()
        body = bytearray(Path(self.track['path']).read_bytes())
        body[-1] ^= 1
        Path(self.track['path']).write_bytes(body)
        process.queue({'phase': 'ready', 'timelinePath': str(path)})
        process.finish()
        self.assertTrue(path.is_file())
        self.assertNotIn(self.track['versionId'], self.timelines)
        self.assertIsNone(self.library.timeline(self.track['versionId']))
        self.assertEqual(self.published, [])
        self.assertEqual(self.manager.reports[self.track['versionId']]['phase'], 'unknown')

    def test_result_outside_job_directory_is_rejected_without_binding(self):
        process = self.start()
        path = self.result_file(inside=False)
        process.queue({'phase': 'ready', 'timelinePath': str(path)})
        process.finish()
        self.assertEqual(self.timelines, {})
        self.assertEqual(self.published, [])
        self.assertEqual(self.manager.reports[self.track['versionId']]['phase'], 'unknown')

    def test_wrong_version_result_is_rejected_before_library_mutation(self):
        process = self.start()
        payload = self.payload()
        payload['versionId'] = 'sha256:' + 'a' * 64
        process.queue({'phase': 'ready', 'timelinePath': str(self.result_file(payload))})
        process.finish()
        self.assertEqual(self.timelines, {})
        self.assertEqual(self.published, [])
        self.assertIsNone(self.library.get_track(self.track['versionId']))

    def test_known_audio_duration_cannot_be_overwritten_by_worker_result(self):
        process = self.start()
        payload = self.payload()
        payload['durationMs'] = 9000
        process.queue({'phase': 'ready', 'timelinePath': str(self.result_file(payload))})
        process.finish()
        self.assertNotIn(self.track['versionId'], self.timelines)
        self.assertEqual(self.published, [])
        self.assertIsNone(self.library.timeline(self.track['versionId']))

    def test_manager_correction_then_utf8_srt_and_lrc_export_uses_bound_version(self):
        original = self.cache()
        reference = self.root / '用户参考歌词.txt'
        reference.write_text('\ufeff往事随风', encoding='utf-8')
        self.manager.correct('A', self.track, str(reference))
        version = self.track['versionId']
        corrected = self.library.timeline(version)
        self.assertEqual(corrected['segments'][0]['text'], '往事随风')
        self.assertEqual(corrected['segments'][0]['startMs'], original['segments'][0]['startMs'])
        self.assertEqual(corrected['segments'][0]['endMs'], original['segments'][0]['endMs'])
        self.assertEqual(corrected['timingProvenance'], 'corrected_provider_token')
        self.assertEqual(len(self.published), 1)
        deck = self.manager.view({'A': self.track})['decks']['A']
        self.assertEqual(deck['phase'], 'corrected')
        self.assertEqual(deck['correctionSummary'], '已改 1 句，待复核 0 句')
        for format in ('srt', 'lrc'):
            target = self.root / '输出' / ('当前歌词.' + format)
            result = self.manager.export('A', self.track, str(target), format)
            self.assertEqual(result, str(target.resolve()))
            body = target.read_bytes()
            self.assertFalse(body.startswith(b'\xef\xbb\xbf'))
            text = body.decode('utf-8')
            self.assertIn('往事随风', text)
            self.assertNotIn('往事随疯', text)
            self.assertIn('00:00:01,000 --> 00:00:02,000' if format == 'srt' else '[00:01.000]', text)

    def test_reference_selected_before_cloud_result_publishes_only_corrected_timeline(self):
        reference = self.root / '提前选择的参考.txt'
        reference.write_text('\ufeff往事随风', encoding='utf-8')
        self.manager.correct('A', self.track, str(reference))
        self.assertEqual(self.timelines, {})
        self.assertEqual(self.published, [])
        self.assertEqual(self.manager.view({'A': self.track})['decks']['A']['referencePath'], str(reference))
        saved = self.manager._receipt(self.track['versionId']).with_name('reference.json')
        self.assertTrue(saved.exists())
        self.assertNotIn('往事随风', saved.read_text(encoding='utf-8'))
        self.manager.shutdown()
        self.manager = self.new_manager()
        self.assertEqual(self.manager.view({'A': self.track})['decks']['A']['referencePath'], str(reference))
        process = self.start()
        process.queue({'phase': 'ready', 'timelinePath': str(self.result_file())})
        process.finish()
        version = self.track['versionId']
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.published[0][1]['segments'][0]['text'], '往事随风')
        self.assertEqual(self.library.timeline(version)['segments'][0]['text'], '往事随风')
        self.assertEqual(self.timelines[version]['segments'][0]['text'], '往事随风')
        target = self.root / '已纠错.srt'
        self.manager.export('A', self.track, str(target), 'srt')
        self.assertIn('往事随风', target.read_text(encoding='utf-8'))
        self.assertNotIn('往事随疯', target.read_text(encoding='utf-8'))

    def test_reference_changed_while_cloud_running_retains_raw_without_publishing(self):
        reference = self.root / '不能悄悄改变的参考.txt'
        reference.write_text('往事随风', encoding='utf-8')
        self.manager.correct('A', self.track, str(reference))
        process = self.start()
        raw_path = self.result_file()
        reference.write_text('完全无关的新参考', encoding='utf-8')
        process.queue({'phase': 'ready', 'timelinePath': str(raw_path)})
        process.finish()
        self.assertEqual(self.timelines, {})
        self.assertEqual(self.published, [])
        self.assertIsNone(self.library.timeline(self.track['versionId']))
        self.assertEqual(json.loads(raw_path.read_text())['segments'][0]['text'], '往事随疯')
        self.assertEqual(self.manager.reports[self.track['versionId']]['phase'], 'unknown')
        self.assertIn('参考文本在识别期间变化', self.manager.reports[self.track['versionId']]['detail'])

    def test_preselected_ambiguous_reference_publishes_review_metadata_without_guess(self):
        reference = self.root / '提前选择重复副歌.txt'
        reference.write_text('往事随风\n往事随风', encoding='utf-8')
        self.manager.correct('A', self.track, str(reference))
        process = self.start()
        process.queue({'phase': 'ready', 'timelinePath': str(self.result_file())})
        process.finish()
        self.assertEqual(len(self.published), 1)
        result = self.published[0][1]
        self.assertEqual(result['segments'][0]['text'], '往事随疯')
        self.assertEqual(result['correction']['status'], 'needs_review')
        self.assertEqual(self.library.timeline(self.track['versionId']), result)
        deck = self.manager.view({'A': self.track})['decks']['A']
        self.assertEqual(deck['phase'], 'needs_review')
        self.assertEqual(deck['correctionSummary'], '已改 0 句，待复核 1 句')

    def test_uncertain_correction_keeps_asr_and_surfaces_review_in_manager(self):
        original = self.cache()
        reference = self.root / '重复副歌.txt'
        reference.write_text('往事随风\n往事随风', encoding='utf-8')
        self.manager.correct('A', self.track, str(reference))
        current = self.timelines[self.track['versionId']]
        self.assertEqual(current['segments'][0]['text'], original['segments'][0]['text'])
        self.assertEqual(current['correction']['status'], 'needs_review')
        view = self.manager.view({'A': self.track})['decks']['A']
        self.assertEqual(view['phase'], 'needs_review')
        self.assertEqual(view['correctionSummary'], '已改 0 句，待复核 1 句')

    def test_changed_file_cannot_be_corrected_or_exported_from_stale_cache(self):
        original = self.cache()
        reference = self.root / '参考.txt'
        reference.write_text('往事随风', encoding='utf-8')
        Path(self.track['path']).unlink()
        with self.assertRaisesRegex(ValueError, '文件身份'):
            self.manager.correct('A', self.track, str(reference))
        target = self.root / '应拒绝.srt'
        with self.assertRaisesRegex(ValueError, '有效歌词缓存'):
            self.manager.export('A', self.track, str(target), 'srt')
        self.assertFalse(target.exists())
        self.assertEqual(self.timelines[self.track['versionId']], original)
        self.assertEqual(self.published, [])

    def test_shutdown_suppresses_sync_kill_finish_and_late_stdout_publication(self):
        process = self.start()
        path = self.result_file()
        process.queue({'phase': 'ready', 'timelinePath': str(path)}, notify=False)
        process.on_kill = lambda: process.finish(0)
        count = len(self.changes)
        self.manager.shutdown()
        process.readyReadStandardOutput.emit()
        process.finished.emit(0)
        self.manager.timer.timeout.emit()
        self.assertTrue(process.killed)
        self.assertTrue(self.manager.timer.stopped)
        self.assertEqual(self.manager.processes, {})
        self.assertEqual(self.manager._key, '')
        self.assertEqual(self.timelines, {})
        self.assertEqual(self.published, [])
        self.assertEqual(len(self.changes), count)


if __name__ == '__main__':
    unittest.main()
