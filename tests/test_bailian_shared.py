"""Shared single/library credentials, receipts, currency and restart boundaries."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ai_settings import AISettings
from worker.ai_providers import profile
from worker.ai_tasks import run as ai_run
from worker.bailian_preparation import run as batch_run
from tests.test_batch_preparation import BatchPreparationTests as Fixtures
from tests.test_ai_settings import ControllerStub, StudioStub, FakeAIProcess
from tests.test_bailian import BailianTransport, KEY


class MemoryKeychain:
    def __init__(self):
        self.values = {}
        self.fail = ''
        self.reads = []

    def read(self, host):
        self.reads.append(host)
        if self.fail == 'read': raise ValueError('locked')
        return self.values.get(host, '')

    def write(self, host, key):
        if self.fail == 'write': raise ValueError('locked')
        self.values[host] = key

    def delete(self, host):
        if self.fail == 'delete': raise ValueError('locked')
        self.values.pop(host, None)


class SharedBailianTests(unittest.TestCase):
    setUp = Fixtures.setUp
    tearDown = Fixtures.tearDown
    new_manager = Fixtures.new_manager
    factory = Fixtures.factory
    audio = Fixtures.audio
    active = Fixtures.active

    def model(self, store=None, manager=None):
        controller = ControllerStub()
        controller.preparation = manager or self.manager
        model = AISettings(self.root, self.runtime, controller, StudioStub(self.tracks[0]),
                           credential_store=store or MemoryKeychain(), process_factory=FakeAIProcess)
        # Keep QObject signal sources alive.
        model.test_controller = controller
        self.addCleanup(model.shutdown)
        return model

    def configure(self, model, remember=False):
        self.assertTrue(model.configureBailian(KEY, 'https://dashscope.aliyuncs.com', 'fun-asr', 'qwen-plus', remember))

    def plan_batch(self, count=2):
        batch = self.manager.plan_batch(self.tracks[:count])
        while self.manager.processes:
            self.finish()
        return batch

    def finish(self, transport=None, manager=None):
        process = self.active(manager=manager)
        request = process.request
        receipt = process.arguments[process.arguments.index('--receipt')+1]
        result = batch_run(process.action, request, receipt, KEY, transport=transport, poll_limit=1)
        process.queue(result); process.finish()
        return result

    def test_one_configuration_drives_serial_batch_with_cny_and_no_key_leaks(self):
        model = self.model(); self.configure(model)
        self.assertEqual(self.manager._key, KEY)
        self.assertTrue(model.state['speech']['keyReady'] and model.state['text']['keyReady'])
        self.manager.set_budget(1)
        batch = self.plan_batch()
        view = self.manager.batch_view()
        self.assertEqual(view['costUnit'], '元')
        self.assertNotIn('estimatedCostPTC', view)
        self.assertAlmostEqual(view['estimatedCostCNY'], .00132)
        self.assertFalse(view['canStart'])
        self.manager.set_new_submission_enabled(True)
        self.manager.start_batch(batch)
        first = self.active()
        self.assertIn('worker.bailian_preparation', first.arguments)
        self.assertEqual(first.environment.value('BAOFANG_TASK_KEY'), KEY)
        self.assertNotIn(KEY, ' '.join(first.arguments))
        self.finish(BailianTransport())
        self.assertEqual(len(self.manager.processes), 1)
        self.finish(BailianTransport())
        self.assertEqual(self.manager.batch_view()['ready'], 2)
        self.assertEqual(len(self.published), 2)
        self.assertEqual(self.timelines[self.tracks[0]['versionId']]['segments'][0]['words'][0]['startMs'], 50)
        for path in self.runtime.rglob('*.json'):
            self.assertNotIn(KEY, path.read_text())
        self.assertNotIn(KEY, json.dumps(model.state))

    def test_cny_total_budget_and_legacy_currency_are_separate(self):
        self.manager.set_budget(300)
        model = self.model(); self.configure(model)
        self.assertEqual(self.manager.budget, 0)
        self.manager.set_budget(.001)
        self.manager.set_new_submission_enabled(True)
        batch = self.plan_batch()
        self.assertIn('超过剩余额度', self.manager.batch_view()['blockedReason'])
        with self.assertRaises(ValueError): self.manager.start_batch(batch)
        self.assertFalse(self.manager.processes)

    def test_pending_single_task_is_queried_by_batch_and_survives_restart(self):
        model = self.model(); self.configure(model)
        model.planSpeech()
        proc=model.process
        proc.queue(ai_run(proc.request)); proc.finish()
        model.startSpeech()
        request=model.process.request
        result=ai_run(request,KEY,transport=BailianTransport(pending=True),poll_limit=1)
        model.process.queue(result);model.process.finish()
        version=self.tracks[0]['versionId']
        self.assertEqual(Path(request['receipt']),self.manager._receipt(version))
        restarted=self.new_manager(); self.configure(self.model(manager=restarted))
        batch=restarted.plan_batch(self.tracks[:1])
        self.assertEqual(restarted.batch_view()['rows'][0]['phase'],'query_ready')
        restarted.start_batch(batch)
        transport=BailianTransport()
        self.assertEqual(self.active(manager=restarted).action,'query')
        self.finish(transport,manager=restarted)
        self.assertEqual(transport.submissions,0)
        self.assertEqual(len(transport.calls),2)
        self.assertIn(version,self.timelines)

    def test_batch_result_reused_by_single_without_upload(self):
        model=self.model();self.configure(model)
        self.manager.set_budget(1);self.manager.set_new_submission_enabled(True)
        batch=self.plan_batch(1);self.manager.start_batch(batch);self.finish(BailianTransport())
        model.planSpeech()
        self.assertIsNone(model.process)
        self.assertTrue(model.state['canApply'])

    def test_unapplied_single_result_is_reused_by_batch_after_restart(self):
        model = self.model()
        self.configure(model)
        model.planSpeech()
        process = model.process
        process.queue(ai_run(process.request))
        process.finish()
        model.startSpeech()
        process = model.process
        process.queue(ai_run(process.request, KEY, transport=BailianTransport()))
        process.finish()
        version = self.tracks[0]['versionId']
        self.assertIsNone(self.library.timeline(version))
        self.assertIsNotNone(self.library.pending_lyric_revision(version))
        self.assertTrue(model.state['canApply'])

        restarted = self.new_manager()
        restarted.plan_batch([self.tracks[0]])
        view = restarted.batch_view()
        self.assertEqual(view['rows'][0]['phase'], 'candidate_ready')
        self.assertEqual(view['ready'], 0)
        self.assertEqual(view['review'], 1)
        self.assertEqual(view['estimatedCost'], 0)
        self.assertFalse(view['canStart'])
        self.assertFalse(restarted.processes)
        self.assertFalse(restarted.reservations)

    def test_unknown_receipt_and_active_task_never_submit_again(self):
        model=self.model();self.configure(model)
        self.manager.set_budget(1);self.manager.set_new_submission_enabled(True)
        batch=self.plan_batch(1);self.manager.start_batch(batch)
        self.assertFalse(model.configureBailian(KEY,'https://dashscope.aliyuncs.com','fun-asr','qwen-plus'))
        model.clearBailianKey();self.assertEqual(self.manager._key,KEY)
        version=self.tracks[0]['versionId']
        self.manager._receipt(version).write_text(json.dumps({'versionId':version,'config':profile('bailian-asr'),'state':'submit_unknown'}))
        self.finish(BailianTransport())
        batch=self.manager.plan_batch(self.tracks[:1])
        self.assertFalse(self.manager.batch_view()['canStart'])
        with self.assertRaises(ValueError):self.manager.start_batch(batch)

    def test_remember_is_opt_in_restore_shares_key_and_clear_deletes(self):
        store=MemoryKeychain();model=self.model(store);self.configure(model)
        self.assertFalse(store.values)
        self.assertFalse(self.model(store).state['bailian']['keyReady'])
        self.configure(model,True)
        self.assertTrue(model.state['bailian']['remembered'])
        restored=self.model(store)
        self.assertTrue(restored.state['speech']['keyReady'] and restored.state['text']['keyReady'])
        self.assertEqual(self.manager._key,KEY)
        self.assertNotIn(KEY,(self.runtime/'ai-profiles.json').read_text())
        restored.clearBailianKey()
        self.assertFalse(store.values)
        self.assertFalse(restored.state['bailian']['keyReady'])
        self.assertFalse(self.manager._key)
        self.assertFalse(self.model(store).state['bailian']['keyReady'])

    def test_keychain_locked_save_read_delete_fail_without_plaintext_fallback(self):
        store=MemoryKeychain();model=self.model(store)
        store.fail='write';self.configure(model,True)
        self.assertFalse(model.state['bailian']['remembered'])
        self.assertTrue(model.state['bailian']['keyReady'])
        self.assertTrue(model.state['bailian']['keychainNotice'])
        store.fail='';self.configure(model,True)
        store.fail='read';restored=self.model(store)
        self.assertFalse(restored.state['bailian']['keyReady'])
        self.assertTrue(restored.state['bailian']['remembered'])
        store.fail='delete';model.clearBailianKey()
        self.assertTrue(model.state['bailian']['keyReady'])
        self.assertTrue(store.values)
        for path in self.runtime.rglob('*.json'):self.assertNotIn(KEY,path.read_text())

    def test_unremember_deletes_persisted_copy_but_retains_session(self):
        store=MemoryKeychain();model=self.model(store);self.configure(model,True)
        self.configure(model,False)
        self.assertFalse(store.values)
        self.assertTrue(model.state['bailian']['keyReady'])
        self.assertFalse(self.model(store).state['bailian']['keyReady'])

    def test_cny_reservation_and_budget_survive_model_switch_and_restart(self):
        model=self.model();self.configure(model)
        self.manager.set_budget(1);self.manager.set_new_submission_enabled(True)
        batch=self.plan_batch(1);self.manager.start_batch(batch);self.finish(BailianTransport())
        spent=self.manager._spent()
        self.assertGreater(spent,0)
        model.configureBailian('', 'https://dashscope.aliyuncs.com','fun-asr-mtl','qwen-plus')
        self.assertEqual(self.manager.budget,1)
        self.assertEqual(self.manager._spent(),spent)
        self.assertEqual(self.manager.reservations,{})
        restarted=self.new_manager();self.model(manager=restarted)
        self.assertEqual(restarted.budget,1)
        self.assertEqual(restarted._spent(),spent)

    def test_advanced_qwen_key_replacement_updates_batch_and_remembered_copy(self):
        store=MemoryKeychain();model=self.model(store);self.configure(model,True)
        replacement='FAKE_REPLACEMENT_KEY'
        self.assertTrue(model.configure('text','qwen','https://dashscope.aliyuncs.com/compatible-mode/v1','qwen-plus',replacement))
        self.assertEqual(self.manager._key,replacement)
        self.assertEqual(store.values['https://dashscope.aliyuncs.com'],replacement)
        self.assertEqual(model.keys[model._key_slot(model._config('speech'))],replacement)

    def test_remembered_key_is_host_scoped_and_clear_removes_owned_hosts(self):
        store=MemoryKeychain();model=self.model(store);self.configure(model,True)
        workspace='https://test-space.cn-beijing.maas.aliyuncs.com'
        self.assertFalse(model.configureBailian('',workspace,'fun-asr','qwen-plus',True))
        self.assertTrue(model.configureBailian('FAKE_WORKSPACE_KEY',workspace,'fun-asr','qwen-plus',True))
        restored=self.model(store)
        self.assertEqual(self.manager._key,'FAKE_WORKSPACE_KEY')
        self.assertEqual(len(store.values),2)
        restored.clearBailianKey();self.assertFalse(store.values)

    def test_batch_reservation_without_receipt_blocks_single_replay(self):
        model=self.model();self.configure(model)
        receipt=self.manager._receipt(self.tracks[0]['versionId'])
        receipt.parent.mkdir(parents=True,exist_ok=True)
        receipt.with_name('reservation.json').write_text('{}')
        model.planSpeech();proc=model.process;proc.queue(ai_run(proc.request));proc.finish()
        model.startSpeech()
        self.assertIsNone(model.process)
        self.assertIn('提交预留',model.state['detail'])


del Fixtures  # Do not rediscover the imported legacy fixture suite.
