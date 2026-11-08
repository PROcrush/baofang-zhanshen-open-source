"""Persistent batch preparation and offline mode; no network or real subprocess."""
import copy
import json
import socket
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QProcessEnvironment

from app.preparation import PreparationManager
from app.storage import Library, inspect_audio
from tests.test_preparation import FakeProcess, FakeTimer, FAKE_KEY


class BatchPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.runtime = self.root / 'runtime'
        self.library = Library(self.root / 'library')
        self.timelines, self.processes, self.published, self.managers = {}, [], [], []
        self.network_attempts = []
        def forbidden(*args, **kwargs):
            self.network_attempts.append(args)
            raise AssertionError('No cloud IO is allowed in batch tests')
        patchers = [patch('app.preparation.QTimer', FakeTimer),
                    patch('app.preparation.QProcessEnvironment.systemEnvironment',
                          side_effect=QProcessEnvironment),
                    patch('socket.create_connection', forbidden),
                    patch.object(socket.socket, 'connect', forbidden),
                    patch.object(socket.socket, 'connect_ex', forbidden)]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.library.close)
        self.addCleanup(lambda: [manager.shutdown() for manager in self.managers])
        self.manager = self.new_manager()
        self.tracks = [self.audio('预备一.wav', 1), self.audio('预备二.wav', 2),
                       self.audio('预备三.wav', 3)]

    def tearDown(self):
        self.assertEqual(self.network_attempts, [])

    def factory(self, parent):
        process = FakeProcess(parent)
        self.processes.append(process)
        return process

    def new_manager(self):
        result = PreparationManager(self.root, self.runtime, self.library, self.timelines,
                                    lambda: None,
                                    lambda version, timeline: self.published.append((version, copy.deepcopy(timeline))),
                                    process_factory=self.factory)
        self.managers.append(result)
        return result

    def audio(self, name, sample):
        path = self.root / name
        with wave.open(str(path), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(sample.to_bytes(2, 'little', signed=True) * 24000)
        track = inspect_audio(path)
        track['identityVerified'] = True
        self.library.add_track(track)
        return track

    def configure(self, budget=1, manager=None):
        path = self.root / 'fixture-config.json'
        path.write_text(json.dumps({'apiKey': FAKE_KEY, 'budgetLimitPTC': budget}), encoding='utf-8')
        (manager or self.manager).configure_file(str(path))

    def active(self, action=None, manager=None):
        manager = manager or self.manager
        entries = [entry['process'] for entry in manager.processes.values()
                   if action is None or entry['action'] == action]
        self.assertEqual(len(entries), 1)
        return entries[0]

    def finish_preflight(self, process, cost=.1, upload_allowed=True):
        request = process.request
        track = request['track']
        process.queue({'phase': 'planned', 'plan': {
            'versionId': track['versionId'], 'sourcePath': track['path'],
            'durationMs': track['durationMs'], 'estimatedCostPTC': cost,
            'host': request['host'], 'uploadAllowed': upload_allowed}})
        process.finish()

    def plan_all(self, tracks=None, costs=None, manager=None):
        manager = manager or self.manager
        batch_id = manager.plan_batch(tracks or self.tracks)
        costs = iter(costs or [.1] * len(self.tracks))
        while any(entry['action'] == 'preflight' for entry in manager.processes.values()):
            self.finish_preflight(self.active('preflight', manager), next(costs))
        return batch_id

    def payload(self, track):
        return {'schemaVersion': 1, 'versionId': track['versionId'],
                'durationMs': track['durationMs'], 'quality': 'sentence',
                'segments': [{'startMs': 500, 'endMs': 2000, 'text': '本地缓存歌词'}]}

    def cache(self, track):
        timeline = self.payload(track)
        self.library.add_timeline(timeline, track)
        self.timelines[track['versionId']] = timeline

    def receipt(self, track, state='polling', task_id='fixture-task', manager=None):
        manager = manager or self.manager
        path = manager._receipt(track['versionId'])
        path.parent.mkdir(parents=True, exist_ok=True)
        plan = manager.plans.get(track['versionId'], {})
        receipt = {'versionId': track['versionId'], 'taskId': task_id, 'state': state,
                   'host': 'api.302ai.cn', 'durationMs': track['durationMs'],
                   'estimatedCostPTC': .1, 'reservedCostPTC': .1,
                   'plan': dict(plan, versionId=track['versionId'], sourcePath=track['path'],
                                durationMs=track['durationMs'], host='api.302ai.cn')}
        path.write_text(json.dumps(receipt), encoding='utf-8')
        return path

    def finish_ready(self, process, track):
        path = self.manager._receipt(track['versionId']).with_name('timeline.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.payload(track)), encoding='utf-8')
        process.queue({'phase': 'ready', 'timelinePath': str(path)})
        process.finish()

    def start_batch(self, tracks=None, costs=None, budget=1):
        self.configure(budget)
        self.manager.set_new_submission_enabled(True)
        batch_id = self.plan_all(tracks, costs)
        self.manager.start_batch(batch_id)
        return batch_id

    def test_batch_requires_real_indexed_current_identity(self):
        for key, value in [('identityVerified', False), ('versionId', 'demo:A'), ('mtimeNs', 0)]:
            with self.subTest(key=key):
                track = dict(self.tracks[0], **{key: value})
                with self.assertRaises(ValueError):
                    self.manager.plan_batch([track])
        unindexed = dict(self.tracks[0], versionId='sha256:' + 'a' * 64)
        with self.assertRaisesRegex(ValueError, '导入本地曲库'):
            self.manager.plan_batch([unindexed])
        self.assertEqual(self.processes, [])

    def test_deduplicate_exact_content_and_cached_rows_never_spawn(self):
        self.cache(self.tracks[0])
        batch_id = self.plan_all([self.tracks[0], self.tracks[0], self.tracks[1]], costs=[.12])
        view = self.manager.batch_view()
        self.assertEqual(view['id'], batch_id)
        self.assertEqual((view['total'], view['ready'], view['planned']), (2, 1, 1))
        self.assertEqual(view['estimatedCostPTC'], .12)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.processes[0].request['track']['versionId'], self.tracks[1]['versionId'])

    def test_all_cached_batch_is_completed_without_key_or_cloud(self):
        for track in self.tracks:
            self.cache(track)
        self.manager.plan_batch(self.tracks)
        view = self.manager.batch_view()
        self.assertEqual((view['status'], view['ready']), ('completed', 3))
        self.assertFalse(view['canStart'])
        self.assertEqual(self.processes, [])

    def test_legacy_timed_cache_stages_for_review_and_stays_archived_after_use(self):
        from app.speech_cache import speech_cache_path
        track = self.tracks[0]
        version = track['versionId']
        original = dict(self.payload(track), provider='bailian-asr')
        path = speech_cache_path(self.runtime, version)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'timeline': original}))

        self.manager.plan_batch([track])
        self.assertEqual(self.manager.batch_view()['review'], 1)
        self.assertIsNone(self.library.timeline(version))
        self.assertEqual(self.library.pending_lyric_revision(version), original)
        self.assertEqual(self.processes, [])

        self.library.add_timeline(original, track)
        self.cache(track)
        self.manager.plan_batch([track])
        self.assertEqual(self.manager.batch_view()['review'], 0)
        self.assertEqual(self.manager.batch_view()['ready'], 1)
        self.assertEqual(self.processes, [])

    def test_preflights_are_serial_and_carry_no_key(self):
        self.configure()
        self.manager.plan_batch(self.tracks)
        for index in range(3):
            process = self.active('preflight')
            self.assertEqual(process.environment.value('BAOFANG_TASK_KEY'), '')
            self.assertEqual(len(self.processes), index + 1)
            self.finish_preflight(process, .1 * (index + 1))
        self.assertEqual(self.manager.batch_view()['planned'], 3)
        self.assertEqual(self.manager.batch_view()['estimatedCostPTC'], .6)
        self.assertEqual(self.manager.batch_view()['pending'], 0)

    def test_new_submission_gate_applies_to_batch_even_zero_estimate(self):
        self.configure()
        batch_id = self.plan_all(self.tracks[:1], costs=[0])
        with self.assertRaisesRegex(ValueError, '尚未允许'):
            self.manager.start_batch(batch_id)
        self.assertEqual([p.action for p in self.processes], ['preflight'])
        self.assertEqual(self.manager.reservations, {})

    def test_full_batch_budget_is_checked_before_any_reservation(self):
        self.configure(.2)
        self.manager.set_new_submission_enabled(True)
        batch_id = self.plan_all(costs=[.1, .1, .1])
        self.assertFalse(self.manager.batch_view()['canStart'])
        with self.assertRaisesRegex(ValueError, '总预估费用'):
            self.manager.start_batch(batch_id)
        self.assertEqual(self.manager.reservations, {})
        self.assertTrue(all(p.action == 'preflight' for p in self.processes))

    def test_float_budget_boundary_and_single_concurrent_dispatch(self):
        self.start_batch(self.tracks[:2], costs=[.1, .2], budget=.3)
        first = self.active('start')
        self.assertEqual(len(self.manager.reservations), 1)
        self.finish_ready(first, self.tracks[0])
        second = self.active('start')
        self.assertEqual(second.request['versionId'], self.tracks[1]['versionId'])
        self.assertEqual(second.request['maxCostPTC'], .2)
        self.assertAlmostEqual(sum(self.manager.reservations.values()), .3)
        self.finish_ready(second, self.tracks[1])
        self.assertEqual(self.manager.batch_view()['status'], 'completed')
        self.assertEqual(self.manager.batch_view()['ready'], 2)
        self.assertEqual(len(self.published), 2)

    def test_active_song_polls_on_timer_before_next_song(self):
        self.start_batch(self.tracks[:2], costs=[.1, .1])
        first = self.active('start')
        self.receipt(self.tracks[0])
        first.queue({'phase': 'polling', 'taskId': 'fixture-task'})
        first.finish()
        self.assertEqual(len([p for p in self.processes if p.action == 'start']), 1)
        self.assertEqual(self.manager.processes, {})
        self.manager.timer.timeout.emit()
        query = self.active('query')
        self.assertEqual(query.request['versionId'], self.tracks[0]['versionId'])
        self.finish_ready(query, self.tracks[0])
        self.assertEqual(self.active('start').request['versionId'], self.tracks[1]['versionId'])

    def test_pause_finishes_current_song_without_starting_next(self):
        batch_id = self.start_batch(self.tracks[:2], costs=[.1, .1])
        first = self.active('start')
        self.manager.pause_batch()
        self.finish_ready(first, self.tracks[0])
        self.manager.timer.timeout.emit()
        self.assertEqual(self.manager.processes, {})
        self.assertEqual(self.manager.batch_view()['status'], 'paused')
        self.assertTrue(self.manager.batch_view()['canStart'])
        self.manager.start_batch(batch_id)
        self.assertEqual(self.active('start').request['versionId'], self.tracks[1]['versionId'])

    def test_restart_retains_list_but_cannot_auto_charge_or_reuse_old_plan(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        batch_id = self.plan_all()
        self.manager.shutdown()
        count = len(self.processes)
        restarted = self.new_manager()
        view = restarted.batch_view()
        self.assertEqual((view['id'], view['total'], view['status']), (batch_id, 3, 'paused'))
        self.assertFalse(view['canStart'])
        self.assertFalse(restarted.enabled)
        self.configure(manager=restarted)
        restarted.set_new_submission_enabled(True)
        restarted.timer.timeout.emit()
        self.assertEqual(len(self.processes), count)
        with self.assertRaisesRegex(ValueError, '已失效'):
            restarted.start_batch(batch_id)

    def test_unknown_receipt_never_preflight_or_resubmit_and_other_song_continues(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        self.receipt(self.tracks[0], state='unknown', task_id='')
        batch_id = self.plan_all(self.tracks[:2], costs=[.1])
        self.assertEqual(self.manager.batch_view()['rows'][0]['phase'], 'unknown')
        self.manager.start_batch(batch_id)
        self.assertEqual(self.active('start').request['versionId'], self.tracks[1]['versionId'])
        self.assertTrue(all(p.request.get('versionId', p.request.get('track', {}).get('versionId')) !=
                            self.tracks[0]['versionId'] for p in self.processes))

    def test_existing_known_receipt_queries_without_new_submission_permission(self):
        self.configure()
        self.receipt(self.tracks[0])
        batch_id = self.manager.plan_batch(self.tracks[:1])
        self.assertEqual(self.processes, [])
        self.assertTrue(self.manager.batch_view()['canStart'])
        self.manager.start_batch(batch_id)
        query = self.active('query')
        self.assertEqual(query.request['host'], 'api.302ai.cn')
        self.assertFalse(self.manager.enabled)
        self.assertEqual(self.manager.reservations, {})

    def test_unknown_response_after_start_keeps_reservation_without_repeating(self):
        self.start_batch(self.tracks[:2], costs=[.1, .1])
        first = self.active('start')
        self.receipt(self.tracks[0], state='unknown', task_id='')
        first.queue({'phase': 'unknown', 'detail': '上传后的状态不明'})
        first.finish()
        second = self.active('start')
        self.assertEqual(second.request['versionId'], self.tracks[1]['versionId'])
        self.finish_ready(second, self.tracks[1])
        self.manager.timer.timeout.emit()
        self.assertEqual([p.action for p in self.processes].count('start'), 2)
        self.assertIn(self.tracks[0]['versionId'], self.manager.reservations)
        self.assertEqual(self.manager.batch_view()['failed'], 1)

    def test_settings_change_invalidates_whole_batch_before_submission(self):
        for change in [lambda: self.manager.set_route('international'),
                       lambda: self.manager.set_budget(.8),
                       lambda: self.configure(.9)]:
            batch_id = self.plan_all(self.tracks[:1], costs=[.1])
            change()
            with self.assertRaisesRegex(ValueError, '已失效'):
                self.manager.start_batch(batch_id)
        self.assertEqual(self.manager.reservations, {})

    def test_inflight_preflight_cannot_restore_batch_after_route_change(self):
        self.manager.plan_batch(self.tracks)
        process = self.active('preflight')
        self.manager.set_route('international')
        self.finish_preflight(process)
        self.assertEqual(self.manager.processes, {})
        self.assertEqual(self.manager.batch_view()['status'], 'paused')
        self.assertFalse(self.manager.batch_view()['canStart'])
        self.assertEqual(len(self.processes), 1)

    def test_source_change_before_batch_start_blocks_every_new_request(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        batch_id = self.plan_all()
        path = Path(self.tracks[1]['path'])
        body = bytearray(path.read_bytes())
        body[-1] ^= 1
        path.write_bytes(body)
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.manager.start_batch(batch_id)
        self.assertEqual(self.manager.reservations, {})
        self.assertEqual([p.action for p in self.processes], ['preflight'] * 3)

    def test_source_change_after_review_prevents_changed_second_song_from_starting(self):
        self.start_batch(self.tracks[:2], costs=[.1, .1])
        first = self.active('start')
        path = Path(self.tracks[1]['path'])
        path.write_bytes(path.read_bytes() + b'changed')
        self.finish_ready(first, self.tracks[0])
        self.assertEqual(self.manager.processes, {})
        self.assertNotIn(self.tracks[1]['versionId'], self.manager.reservations)
        self.assertEqual(self.manager.batch_view()['failed'], 1)

    def test_service_upload_limit_blocks_batch_before_charging(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        batch_id = self.manager.plan_batch(self.tracks[:1])
        self.finish_preflight(self.active('preflight'), upload_allowed=False)
        with self.assertRaisesRegex(ValueError, '上传限制'):
            self.manager.start_batch(batch_id)
        self.assertEqual(self.manager.reservations, {})

    def test_offline_mode_persists_and_blocks_start_query_and_timer(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        batch_id = self.plan_all(self.tracks[:1], costs=[.1])
        self.receipt(self.tracks[1])
        self.manager.tracks[self.tracks[1]['versionId']] = self.tracks[1]
        self.manager.set_offline_only(True)
        before = len(self.processes)
        self.assertTrue(self.manager.view({})['config']['offlineOnly'])
        self.assertFalse(self.manager.view({'A': self.tracks[1]})['decks']['A']['canQuery'])
        with self.assertRaisesRegex(ValueError, '离线'):
            self.manager.start_batch(batch_id)
        with self.assertRaisesRegex(ValueError, '离线'):
            self.manager.start('A', self.tracks[0], self.manager.plans[self.tracks[0]['versionId']]['planId'])
        with self.assertRaisesRegex(ValueError, '离线'):
            self.manager.query('A', self.tracks[1])
        self.manager.timer.timeout.emit()
        self.configure()
        self.assertEqual(len(self.processes), before)
        self.assertEqual(self.manager.reservations, {})
        restarted = self.new_manager()
        self.assertTrue(restarted.offline_only)
        self.assertFalse(hasattr(restarted.timer, 'interval'))
        restarted.timer.timeout.emit()
        self.assertEqual(len(self.processes), before)

    def test_offline_can_preflight_correct_export_and_reuse_local_cache(self):
        self.manager.set_offline_only(True)
        self.manager.plan_batch(self.tracks[:1])
        self.finish_preflight(self.active('preflight'))
        self.cache(self.tracks[0])
        reference = self.root / '参考.txt'
        reference.write_text('本地缓存歌词\n', encoding='utf-8')
        self.manager.correct('', self.tracks[0], reference)
        result = self.root / '离线字幕.srt'
        self.manager.export('', self.tracks[0], result, 'srt')
        self.assertIn('本地缓存歌词', result.read_text(encoding='utf-8'))
        self.assertEqual([p.action for p in self.processes], ['preflight'])
        self.assertTrue(self.manager.view({'A': self.tracks[0]})['decks']['A']['canExport'])

    def test_switching_offline_kills_network_child_without_publish_or_budget_loss(self):
        self.start_batch(self.tracks[:2], costs=[.1, .1])
        process = self.active('start')
        self.receipt(self.tracks[0])
        process.on_kill = lambda: process.finish()
        self.manager.set_offline_only(True)
        self.assertTrue(process.killed)
        self.assertEqual(self.manager.processes, {})
        self.assertEqual(self.manager.batch_view()['status'], 'paused')
        self.assertEqual(self.manager.reservations[self.tracks[0]['versionId']], .1)
        self.assertTrue(self.manager._receipt(self.tracks[0]['versionId']).exists())
        self.assertEqual(self.published, [])
        before = len(self.processes)
        process.queue({'phase': 'ready', 'timelinePath': '/does/not/exist'})
        self.manager.timer.timeout.emit()
        self.assertEqual(len(self.processes), before)
        self.assertEqual(self.published, [])

    def test_replanning_failed_preflight_can_recover_without_paid_retry(self):
        self.manager.plan_batch(self.tracks[:1])
        process = self.active('preflight')
        process.queue({'phase': 'failed', 'detail': '测试本地预检失败'})
        process.finish(1)
        self.assertEqual(self.manager.batch_view()['failed'], 1)
        self.manager.plan_batch(self.tracks[:1])
        self.finish_preflight(self.active('preflight'))
        self.assertEqual(self.manager.batch_view()['planned'], 1)
        self.assertEqual(self.manager.reservations, {})

    def test_batch_public_and_persistent_state_never_contains_key(self):
        self.configure()
        self.plan_all()
        self.assertNotIn(FAKE_KEY, json.dumps(self.manager.batch_view()))
        source = self.runtime / 'batch-preparation.json'
        self.assertEqual(source.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(FAKE_KEY, source.read_text(encoding='utf-8'))
        self.assertTrue(all('track' not in row for row in self.manager.batch_view()['rows']))

    def test_manual_network_action_cannot_break_batch_single_concurrency(self):
        self.start_batch(self.tracks[:2], costs=[.1, .1])
        first = self.active('start')
        plan = self.manager.plans[self.tracks[1]['versionId']]
        with self.assertRaisesRegex(ValueError, '其他歌曲'):
            self.manager.start('', self.tracks[1], plan['planId'])
        self.receipt(self.tracks[2])
        with self.assertRaisesRegex(ValueError, '其他歌曲'):
            self.manager.query('', self.tracks[2])
        self.manager.tracks[self.tracks[2]['versionId']] = self.tracks[2]
        count = len(self.processes)
        self.manager.timer.timeout.emit()
        self.assertEqual(len(self.processes), count)
        self.assertIs(self.active('start'), first)

    def test_pause_can_query_only_current_accepted_song_then_waits(self):
        self.start_batch(self.tracks[:2], costs=[.1, .1])
        first = self.active('start')
        self.receipt(self.tracks[0])
        self.manager.pause_batch()
        first.queue({'phase': 'polling', 'taskId': 'fixture-task'})
        first.finish()
        self.manager.timer.timeout.emit()
        query = self.active('query')
        self.assertEqual(query.request['versionId'], self.tracks[0]['versionId'])
        self.finish_ready(query, self.tracks[0])
        self.manager.timer.timeout.emit()
        self.assertEqual(self.manager.processes, {})
        self.assertEqual(self.manager.batch_view()['planned'], 1)

    def test_route_change_in_active_batch_stops_next_song_but_query_stays_pinned(self):
        self.start_batch(self.tracks[:2], costs=[.1, .1])
        first = self.active('start')
        self.receipt(self.tracks[0])
        self.manager.set_route('international')
        first.queue({'phase': 'polling', 'taskId': 'fixture-task'})
        first.finish()
        self.manager.timer.timeout.emit()
        query = self.active('query')
        self.assertEqual(query.request['host'], 'api.302ai.cn')
        self.finish_ready(query, self.tracks[0])
        self.manager.timer.timeout.emit()
        self.assertEqual(self.manager.processes, {})
        self.assertEqual(self.manager.batch_view()['status'], 'paused')
        self.assertFalse(self.manager.batch_view()['canStart'])

    def test_wrong_batch_id_and_shutdown_prevent_new_dispatch(self):
        self.configure()
        self.manager.set_new_submission_enabled(True)
        batch_id = self.plan_all(self.tracks[:1], costs=[.1])
        with self.assertRaisesRegex(ValueError, '批次已变化'):
            self.manager.start_batch('older-batch-id')
        before = len(self.processes)
        self.manager.shutdown()
        with self.assertRaisesRegex(ValueError, '已关闭'):
            self.manager.start_batch(batch_id)
        self.manager.timer.timeout.emit()
        self.assertEqual(len(self.processes), before)
        self.assertEqual(self.manager.reservations, {})


if __name__ == '__main__':
    unittest.main()
